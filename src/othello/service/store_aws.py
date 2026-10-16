"""The job store on AWS: a DynamoDB table for the job records and an S3 bucket for the files.

Same methods as ``LocalJobStore``, so the API and the worker do not know the difference.

DynamoDB item (one per job, key ``job_id``)::

    job         the job record as JSON text (floats are not allowed in DynamoDB, so it is stored as text)
    events      list of JSON texts, one per progress step (appended atomically, capped at MAX_EVENTS)
    cancel      true once a cancel was asked for
    active      "1" while the job is queued or running, absent afterwards (this feeds the ActiveIndex)
    expires_at  epoch seconds; DynamoDB's TTL deletes the item some time after this

S3 keys are ``jobs/<job_id>/<file name>``.  A bucket rule deletes them after two days, as a safety net
next to the TTL.  The names below must match ``infra/template.yaml`` (a test checks that).
"""

from __future__ import annotations

import json
import time
from typing import Any

from othello.service.store import ACTIVE, FILE_KINDS, MAX_EVENTS, Job, valid_job_id

TABLE_KEY = "job_id"
ACTIVE_INDEX = "ActiveIndex"
ACTIVE_ATTRIBUTE = "active"
TTL_ATTRIBUTE = "expires_at"
KEY_PREFIX = "jobs/"


class AwsJobStore:
    def __init__(
        self,
        table_name: str,
        bucket: str,
        retention_s: float = 24 * 3600.0,
        *,
        dynamodb: Any = None,
        s3: Any = None,
        clock: Any = time.time,
    ):
        if dynamodb is None or s3 is None:
            import boto3  # imported here so the rest of the service works without AWS libraries

            dynamodb = dynamodb or boto3.client("dynamodb")
            s3 = s3 or boto3.client("s3")
        self.table, self.bucket, self.retention_s = table_name, bucket, retention_s
        self.db, self.s3, self.clock = dynamodb, s3, clock

    # ---- small helpers

    @staticmethod
    def _key(job_id: str) -> dict[str, Any]:
        if not valid_job_id(
            job_id
        ):  # never build a key from anything but a plain job id
            raise ValueError("invalid job id")
        return {TABLE_KEY: {"S": job_id}}

    @staticmethod
    def _s3_key(job_id: str, kind: str) -> str:
        return f"{KEY_PREFIX}{job_id}/{FILE_KINDS[kind][0]}"

    def _expiry(self) -> dict[str, str]:
        return {"N": str(int(self.clock() + self.retention_s))}

    @staticmethod
    def _error_code(problem: Exception) -> str:
        return getattr(problem, "response", {}).get("Error", {}).get("Code", "")

    # ---- the job record

    def create(self, job: Job, input_bytes: bytes) -> None:
        self.put_file(job.job_id, "input", input_bytes)
        item: dict[str, Any] = {
            **self._key(job.job_id),
            "job": {"S": job.model_dump_json()},
            TTL_ATTRIBUTE: self._expiry(),
        }
        if not job.finished:
            item[ACTIVE_ATTRIBUTE] = {"S": "1"}
        self.db.put_item(
            TableName=self.table,
            Item=item,
            ConditionExpression="attribute_not_exists(#id)",
            ExpressionAttributeNames={"#id": TABLE_KEY},
        )

    def get(self, job_id: str) -> Job | None:
        if not valid_job_id(job_id):
            return None
        reply = self.db.get_item(
            TableName=self.table, Key=self._key(job_id), ConsistentRead=True
        )
        item = reply.get("Item")
        if not item:
            return None
        try:
            return Job.model_validate_json(item["job"]["S"])
        except (KeyError, ValueError):
            return None

    def save(self, job: Job) -> None:
        names: dict[str, str] = {}
        values: dict[str, Any] = {
            ":j": {"S": job.model_dump_json()},
            ":t": self._expiry(),
        }
        expression = "SET #job = :j, #ttl = :t"
        names["#ttl"] = TTL_ATTRIBUTE
        names["#job"] = "job"
        if job.status in ACTIVE:
            expression += ", #act = :a"
            names["#act"] = ACTIVE_ATTRIBUTE
            values[":a"] = {"S": "1"}
        else:
            expression += " REMOVE #act"
            names["#act"] = ACTIVE_ATTRIBUTE
        self.db.update_item(
            TableName=self.table,
            Key=self._key(job.job_id),
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    # ---- progress events

    def append_event(self, job_id: str, event: dict[str, Any]) -> None:
        text = json.dumps(event, separators=(",", ":"))
        try:
            self.db.update_item(
                TableName=self.table,
                Key=self._key(job_id),
                UpdateExpression="SET #ev = list_append(if_not_exists(#ev, :none), :new)",
                ConditionExpression="attribute_not_exists(#ev) OR size(#ev) < :max",
                ExpressionAttributeNames={"#ev": "events"},
                ExpressionAttributeValues={
                    ":none": {"L": []},
                    ":new": {"L": [{"S": text}]},
                    ":max": {"N": str(MAX_EVENTS)},
                },
            )
        except Exception as problem:
            if self._error_code(problem) != "ConditionalCheckFailedException":
                raise  # the cap was reached: further steps are simply dropped

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        reply = self.db.get_item(
            TableName=self.table,
            Key=self._key(job_id),
            ProjectionExpression="#ev",
            ExpressionAttributeNames={"#ev": "events"},
            ConsistentRead=True,
        )
        entries = reply.get("Item", {}).get("events", {}).get("L", [])
        found = []
        for entry in entries[max(after, 0) :]:
            try:
                found.append(json.loads(entry["S"]))
            except (KeyError, ValueError):
                break
        return found

    # ---- cancel flag

    def request_cancel(self, job_id: str) -> None:
        self.db.update_item(
            TableName=self.table,
            Key=self._key(job_id),
            UpdateExpression="SET #cn = :t",
            ExpressionAttributeNames={"#cn": "cancel"},
            ExpressionAttributeValues={":t": {"BOOL": True}},
        )

    def cancel_requested(self, job_id: str) -> bool:
        reply = self.db.get_item(
            TableName=self.table,
            Key=self._key(job_id),
            ProjectionExpression="#cn",
            ExpressionAttributeNames={"#cn": "cancel"},
            ConsistentRead=True,
        )
        return bool(reply.get("Item", {}).get("cancel", {}).get("BOOL", False))

    # ---- files

    def put_file(self, job_id: str, kind: str, data: bytes) -> None:
        self._key(job_id)  # validates the id
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self._s3_key(job_id, kind),
            Body=data,
            ContentType=FILE_KINDS[kind][1],
            ServerSideEncryption="AES256",
        )

    def get_file(self, job_id: str, kind: str) -> bytes | None:
        if kind not in FILE_KINDS or not valid_job_id(job_id):
            return None
        try:
            return self.s3.get_object(
                Bucket=self.bucket, Key=self._s3_key(job_id, kind)
            )["Body"].read()
        except Exception as problem:
            if self._error_code(problem) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise

    # ---- housekeeping

    def count_active(self) -> int:
        total, start = 0, None
        while True:
            args: dict[str, Any] = {
                "TableName": self.table,
                "IndexName": ACTIVE_INDEX,
                "KeyConditionExpression": "#a = :a",
                "ExpressionAttributeNames": {"#a": ACTIVE_ATTRIBUTE},
                "ExpressionAttributeValues": {":a": {"S": "1"}},
                "Select": "COUNT",
            }
            if start:
                args["ExclusiveStartKey"] = start
            reply = self.db.query(**args)
            total += reply["Count"]
            start = reply.get("LastEvaluatedKey")
            if not start:
                return total

    def delete(self, job_id: str) -> None:
        self.db.delete_item(TableName=self.table, Key=self._key(job_id))
        prefix = f"{KEY_PREFIX}{job_id}/"
        listing = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        keys = [{"Key": entry["Key"]} for entry in listing.get("Contents", [])]
        if keys:
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})

    def delete_older_than(self, seconds: float) -> int:
        """Rarely needed: the table's TTL and the bucket rule already clean up. Kept for manual clean-ups."""
        cutoff, removed, start = self.clock() - seconds, 0, None
        while True:
            args: dict[str, Any] = {
                "TableName": self.table,
                "ProjectionExpression": "#id, #job",
                "ExpressionAttributeNames": {"#id": TABLE_KEY, "#job": "job"},
            }
            if start:
                args["ExclusiveStartKey"] = start
            reply = self.db.scan(**args)
            for item in reply.get("Items", []):
                try:
                    job = Job.model_validate_json(item["job"]["S"])
                except (KeyError, ValueError):
                    continue
                if job.finished and (job.finished_at or job.created_at) < cutoff:
                    self.delete(job.job_id)
                    removed += 1
            start = reply.get("LastEvaluatedKey")
            if not start:
                return removed
