# Deploying to AWS

This puts the web API ([API.md](API.md)) on AWS, in `us-east-1` (the same region as BiteCheck). Everything is described in files; nothing is clicked together by hand. Only the account owner can do the two one-time steps below.

## What gets created

| Piece | What it is for |
|---|---|
| `JobsTable` (DynamoDB) | remembers every job: its state and progress steps. Old jobs delete themselves after a day |
| `JobsBucket` (S3) | a private folder for the result files. Also cleaned up after two days |
| `ApiFunction` (Lambda) | answers web requests: upload, status, progress, results, cancel |
| `WorkerFunction` (Lambda) | solves one plan (up to a few minutes). Started by the API, which does not wait for it |
| `HttpApi` | the public web address in front of the API function (limited to about 10 requests a second) |
| two log groups | logs, kept for 14 days |

Both functions run from one container image built from `infra/Dockerfile`. You pay only while something runs; for a demo this is a few cents.

## One-time setup (account owner)

**1. Let GitHub deploy without a stored password.** Check whether this AWS account already trusts GitHub (BiteCheck may have set that up):

```bash
aws iam list-open-id-connect-providers --region us-east-1
```

If a line ends in `token.actions.githubusercontent.com`, copy its full ARN. Then create the role:

```bash
aws cloudformation deploy --region us-east-1 --stack-name othello-cable-ring-github-role --template-file infra/github-deploy-role.yaml --capabilities CAPABILITY_NAMED_IAM
```

Add `--parameter-overrides ExistingOidcProviderArn=<the ARN>` to that command if the provider already exists. When it finishes, print the role address:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name othello-cable-ring-github-role --query "Stacks[0].Outputs[0].OutputValue" --output text
```

The role can only be used by workflows of the `Othello-HackHive/Othello` repository on the `main` branch.

**2. Tell GitHub.** In the repository: Settings, Secrets and variables, Actions.

| Where | Name | Value |
|---|---|---|
| Variables | `AWS_DEPLOY_ROLE_ARN` | the role address from step 1 (until this exists, the Deploy workflow does nothing) |
| Variables | `ALLOWED_ORIGINS` | the website address(es) allowed to call the API, comma-separated, e.g. the Amplify address. Without it only `localhost` works |
| Variables | `WORKER_CONCURRENCY` | optional. `5` caps how many plans are solved at once (the account must allow it). Leave empty for no cap |
| Secrets | `OTHELLO_API_KEY` | optional. A password the frontend must send in `x-api-key`. See "Safety" below |

## Deploying

**Automatic:** every merge to `main` whose CI run is green triggers the Deploy workflow. **By hand:** Actions tab, Deploy, Run workflow. The last step of the workflow calls `/health` on the new address and fails the run if it does not answer; the address is printed in the log as `API address: ...`.

**From your own computer** (useful for the very first deploy; needs Docker running, and the SAM tool you already use for BiteCheck):

```bash
sam build --template-file infra/template.yaml
sam deploy --config-file infra/samconfig.toml --parameter-overrides "AllowedOrigins=https://<your-amplify-address>"
```

## After the first deploy

1. Try it: `curl https://<api-address>/health` should answer `{"status":"ok","version":"1"}`. Then upload a sample: `curl -X POST https://<api-address>/api/jobs -H "Content-Type: application/json" --data-binary @tests/data/uploads/one_room.json`, and follow the `poll` address from the answer.
2. Give Ritvik the API address (the `ApiUrl` output, no trailing slash). In Amplify it belongs in an environment variable of the frontend, and the Amplify website address must be in `ALLOWED_ORIGINS`, otherwise browsers refuse the answers.
3. If you set `OTHELLO_API_KEY`, give the same value to Ritvik for the `x-api-key` header.

## Settings you can change (template parameters)

`AllowedOrigins`, `ApiKey`, `DefaultBudgetSeconds` (60), `MaxBudgetSeconds` (120, at most 240), `MaxActiveJobs` (10), `RetentionSeconds` (86400) and `WorkerConcurrency` (0 = no cap). The workflow passes the first, second and last from the GitHub values above; for the others edit `infra/template.yaml` and merge.

## Looking at what happened

```bash
aws logs tail /aws/lambda/othello-cable-ring-worker --follow --region us-east-1
aws logs tail /aws/lambda/othello-cable-ring-api --follow --region us-east-1
```

A job that went wrong also has its own `failure` file and `error` in the API answer.

## Taking it down

Empty the files bucket first (a bucket with files in it cannot be deleted), then delete the stack:

```bash
aws s3 rm s3://<JobsBucketName> --recursive --region us-east-1
sam delete --stack-name othello-cable-ring --region us-east-1
```

To go back to an earlier version, re-run the Deploy workflow on an older commit, or `git revert` and merge.

## Safety

- The API only reads JSON (never runs uploaded code), limits upload size, settings and the number of jobs, and hides internals in error answers.
- Job ids are random 32-character codes; only people who know one can read that job. Uploaded plans are never served back.
- `OTHELLO_API_KEY` is checked by the server, but a key placed in a public web page can be read by anyone, so treat it as a speed bump. The real limits are the throttle, the job cap and the time limits.
- The AWS role used by GitHub lasts one hour per run, works only for this repository's `main` branch, and may only touch resources whose names start with `othello-cable-ring`.

## If something fails

| Symptom | Likely cause |
|---|---|
| Deploy workflow is skipped | `AWS_DEPLOY_ROLE_ARN` is not set yet, or CI on `main` was not green |
| `AccessDenied` in the Deploy log | the role is missing a permission: send the action name from the message (it looks like `lambda:CreateFunction`) and it gets added to `infra/github-deploy-role.yaml` |
| Browser says "blocked by CORS" | the website address is not in `ALLOWED_ORIGINS` (it must match exactly, with `https://` and no trailing slash) |
| `401` from the API | `x-api-key` missing or different from `OTHELLO_API_KEY` |
| `429 BUSY` | `MaxActiveJobs` jobs are already queued or running |
| `503 SERVICE_UNAVAILABLE` | the API could not start the worker: check that the worker function exists and the API function may invoke it |
| A job stays `running`, then becomes `failed` / `TIMED_OUT` | the worker stopped or ran out of time: read the worker log |
| Reserving worker capacity fails | the account's concurrency limit is too low: set `WORKER_CONCURRENCY` to empty |
