"""T26: the deployment files (container, AWS template, deploy workflow) agree with the code.

Nothing here talks to AWS or Docker: the files are read as text and checked against the Python code
they configure, so a renamed table, a wrong command or a missing setting is found in the pull request
and not during a deploy.
"""

import importlib
import re
import tomllib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"


class CfnLoader(yaml.SafeLoader):
    """YAML with CloudFormation's short tags (!Ref, !Sub, !If...) read as plain {"Ref": ...} dictionaries."""


def _tag(loader, suffix, node):
    name = (
        suffix if suffix.startswith("Ref") or suffix == "Condition" else f"Fn::{suffix}"
    )
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {"Ref" if name == "Ref" else name: value}


CfnLoader.add_multi_constructor("!", _tag)


def load(name):
    return yaml.load((INFRA / name).read_text(encoding="utf-8"), Loader=CfnLoader)


@pytest.fixture(scope="module")
def template():
    return load("template.yaml")


@pytest.fixture(scope="module")
def resources(template):
    return template["Resources"]


def of_type(resources, kind):
    return {name: res for name, res in resources.items() if res["Type"] == kind}


# --------------------------------------------------- the template and the code agree


def test_the_table_matches_what_the_store_expects(resources):
    from othello.service.store_aws import (
        ACTIVE_ATTRIBUTE,
        ACTIVE_INDEX,
        TABLE_KEY,
        TTL_ATTRIBUTE,
    )

    (table,) = of_type(resources, "AWS::DynamoDB::Table").values()
    props = table["Properties"]
    assert props["BillingMode"] == "PAY_PER_REQUEST"
    assert props["KeySchema"] == [{"AttributeName": TABLE_KEY, "KeyType": "HASH"}]
    (index,) = props["GlobalSecondaryIndexes"]
    assert index["IndexName"] == ACTIVE_INDEX and index["KeySchema"] == [
        {"AttributeName": ACTIVE_ATTRIBUTE, "KeyType": "HASH"}
    ]
    assert index["Projection"] == {"ProjectionType": "KEYS_ONLY"}
    assert props["TimeToLiveSpecification"] == {
        "AttributeName": TTL_ATTRIBUTE,
        "Enabled": True,
    }
    declared = {a["AttributeName"] for a in props["AttributeDefinitions"]}
    assert declared == {TABLE_KEY, ACTIVE_ATTRIBUTE}  # only key attributes are declared


def test_the_bucket_is_private_encrypted_and_cleans_itself_up(resources):
    from othello.service.store_aws import KEY_PREFIX

    (bucket,) = of_type(resources, "AWS::S3::Bucket").values()
    props = bucket["Properties"]
    assert (
        all(props["PublicAccessBlockConfiguration"].values())
        and len(props["PublicAccessBlockConfiguration"]) == 4
    )
    assert (
        props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
            "ServerSideEncryptionByDefault"
        ]["SSEAlgorithm"]
        == "AES256"
    )
    rules = {r["Id"]: r for r in props["LifecycleConfiguration"]["Rules"]}
    assert (
        rules["expire-job-files"]["Prefix"] == KEY_PREFIX
        and rules["expire-job-files"]["ExpirationInDays"] >= 1
    )


def test_each_function_runs_the_right_entry_point_and_both_exist_in_the_code(resources):
    functions = of_type(resources, "AWS::Serverless::Function")
    assert set(functions) == {"ApiFunction", "WorkerFunction"}
    expected = {"ApiFunction": "handler", "WorkerFunction": "worker_handler"}
    for name, function in functions.items():
        (command,) = function["Properties"]["ImageConfig"]["Command"]
        module, attribute = command.rsplit(".", 1)
        assert attribute == expected[name] and callable(
            getattr(importlib.import_module(module), attribute)
        )
        assert function["Properties"]["PackageType"] == "Image"
        assert (
            function["Metadata"]["Dockerfile"] == "infra/Dockerfile"
            and function["Metadata"]["DockerContext"] == ".."
        )


def test_every_environment_variable_the_code_reads_is_set_by_the_template(
    resources, template
):
    handler_source = (ROOT / "src/othello/service/lambda_handler.py").read_text(
        encoding="utf-8"
    )
    needed = {"JOBS_TABLE", "JOBS_BUCKET"} & set(
        re.findall(r'os\.environ\["([A-Z_]+)"\]', handler_source)
    )
    assert (
        needed == {"JOBS_TABLE", "JOBS_BUCKET"} and "WORKER_FUNCTION" in handler_source
    )
    api_env = resources["ApiFunction"]["Properties"]["Environment"]["Variables"]
    worker_env = resources["WorkerFunction"]["Properties"]["Environment"]["Variables"]
    assert {"JOBS_TABLE", "JOBS_BUCKET", "WORKER_FUNCTION"} <= set(api_env)
    assert {"JOBS_TABLE", "JOBS_BUCKET"} <= set(
        worker_env
    ) and "WORKER_FUNCTION" not in worker_env


def test_the_service_settings_in_the_template_are_ones_the_code_understands(template):
    config_source = (ROOT / "src/othello/service/config.py").read_text(encoding="utf-8")
    known = set(re.findall(r'"(OTHELLO_API_[A-Z_]+)"', config_source))
    global_env = template["Globals"]["Function"]["Environment"]["Variables"]
    ours = {name for name in global_env if name.startswith("OTHELLO_API_")}
    assert ours and ours <= known
    assert {
        "OTHELLO_API_ORIGINS",
        "OTHELLO_API_KEY",
        "OTHELLO_API_MAX_BUDGET_S",
        "OTHELLO_API_RETENTION_S",
    } <= ours


def test_template_defaults_equal_the_code_defaults(template):
    from othello.service.config import ServiceConfig

    params, code = template["Parameters"], ServiceConfig()
    assert params["DefaultBudgetSeconds"]["Default"] == code.default_budget_s
    assert params["MaxBudgetSeconds"]["Default"] == code.max_budget_s
    assert params["MaxActiveJobs"]["Default"] == code.max_active_jobs
    assert params["RetentionSeconds"]["Default"] == code.retention_s
    assert params["AllowedOrigins"]["Default"].split(",") == list(code.allowed_origins)


def test_the_time_limits_leave_room_for_the_work(resources, template):
    api, worker = (
        resources["ApiFunction"]["Properties"],
        resources["WorkerFunction"]["Properties"],
    )
    assert api["Timeout"] < 29  # the web gateway gives up at 29 seconds
    max_budget = template["Parameters"]["MaxBudgetSeconds"]
    assert (
        max_budget["MaxValue"] + 60 <= worker["Timeout"] <= 900
    )  # solve time + loading + writing
    assert (
        max_budget["Default"] <= max_budget["MaxValue"] and worker["MemorySize"] >= 2048
    )


def test_a_failed_worker_is_never_retried_because_that_would_run_a_job_twice(resources):
    assert (
        resources["WorkerFunction"]["Properties"]["EventInvokeConfig"][
            "MaximumRetryAttempts"
        ]
        == 0
    )


def test_the_api_key_is_secret_and_the_web_gateway_is_throttled(template, resources):
    assert template["Parameters"]["ApiKey"]["NoEcho"] is True
    (api,) = of_type(resources, "AWS::Serverless::HttpApi").values()
    assert 0 < api["Properties"]["DefaultRouteSettings"]["ThrottlingRateLimit"] <= 50
    assert (
        "CorsConfiguration" not in api["Properties"]
    )  # the application answers CORS itself, once


def test_the_functions_may_only_use_their_own_table_bucket_and_worker(resources):
    api = resources["ApiFunction"]["Properties"]["Policies"]
    worker = resources["WorkerFunction"]["Properties"]["Policies"]
    kinds = lambda policies: {next(iter(p)) for p in policies}
    assert kinds(api) == {"DynamoDBCrudPolicy", "S3CrudPolicy", "LambdaInvokePolicy"}
    assert kinds(worker) == {"DynamoDBCrudPolicy", "S3CrudPolicy"}
    assert not any(
        isinstance(p, str) for p in api + worker
    )  # no whole-account managed policies


def test_both_routes_reach_the_api_function_and_logs_expire(resources):
    events = resources["ApiFunction"]["Properties"]["Events"]
    assert {
        (e["Properties"]["Path"], e["Properties"]["Method"]) for e in events.values()
    } == {("/", "ANY"), ("/{proxy+}", "ANY")}
    for log in of_type(resources, "AWS::Logs::LogGroup").values():
        assert 1 <= log["Properties"]["RetentionInDays"] <= 30


def test_the_template_outputs_the_address_the_frontend_needs(template):
    assert {"ApiUrl", "JobsTableName", "JobsBucketName", "WorkerFunctionName"} <= set(
        template["Outputs"]
    )
    assert "execute-api" in str(template["Outputs"]["ApiUrl"]["Value"])


# --------------------------------------------------- the deploy settings


def test_the_deploy_settings_match_the_account_and_the_workflow():
    config = tomllib.loads((INFRA / "samconfig.toml").read_text(encoding="utf-8"))[
        "default"
    ]
    assert (
        config["global"]["parameters"]["region"] == "us-east-1"
    )  # same region as BiteCheck
    assert config["global"]["parameters"]["stack_name"] == "othello-cable-ring"
    deploy = config["deploy"]["parameters"]
    assert (
        deploy["capabilities"] == "CAPABILITY_IAM"
        and deploy["resolve_image_repos"] is True
    )
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert (
        "STACK_NAME: othello-cable-ring" in workflow
        and "AWS_REGION: us-east-1" in workflow
    )
    assert "othello-cable-ring" in (INFRA / "github-deploy-role.yaml").read_text(
        encoding="utf-8"
    )


def test_no_secret_is_written_in_the_deployment_files():
    for path in [
        *INFRA.iterdir(),
        *(ROOT / ".github/workflows").iterdir(),
        ROOT / ".dockerignore",
    ]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"AKIA[0-9A-Z]{16}", text), path.name
        assert (
            "aws_secret_access_key" not in text.lower()
            and "AWS_SECRET_ACCESS_KEY" not in text
        ), path.name
        assert "AWS_ACCESS_KEY_ID" not in text, path.name


# --------------------------------------------------- the container


def test_the_image_installs_the_project_with_pictures_and_starts_the_api_by_default():
    text = (INFRA / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"FROM public\.ecr\.aws/lambda/python:3\.1[1-9]", text)
    assert "[viz]" in text and "COPY src" in text and "COPY pyproject.toml" in text
    assert (
        'CMD ["othello.service.lambda_handler.handler"]' in text
        and "MPLCONFIGDIR=/tmp" in text
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project["requires-python"].startswith(">=3.11")


def test_the_image_does_not_carry_data_tests_secrets_or_the_frontend():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for name in (".git", "data", "frontend", "tests", "out", "*.pkl", "*.local.*"):
        assert name in ignored, name


# --------------------------------------------------- GitHub


def test_the_deploy_workflow_uses_a_temporary_identity_and_waits_for_green_checks():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    )
    assert (
        workflow["permissions"]["id-token"] == "write"
        and workflow["permissions"]["contents"] == "read"
    )
    trigger = (
        workflow[True] if True in workflow else workflow["on"]
    )  # YAML reads a bare "on" as True
    assert trigger["workflow_run"]["workflows"] == ["CI"] and trigger["workflow_run"][
        "branches"
    ] == ["main"]
    assert (
        "workflow_dispatch" in trigger
        and workflow["concurrency"]["cancel-in-progress"] is False
    )
    job = workflow["jobs"]["deploy"]
    assert (
        "vars.AWS_DEPLOY_ROLE_ARN" in job["if"]
        and "conclusion == 'success'" in job["if"]
        # automatic deploys are opt-in: the automatic branch of the condition needs AUTO_DEPLOY
        and "vars.AUTO_DEPLOY == 'true' && github.event.workflow_run.conclusion"
        in job["if"]
    )
    steps = {step.get("name", ""): step for step in job["steps"]}
    sign_in = next(
        s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials")
    )
    assert sign_in["with"]["role-to-assume"] == "${{ vars.AWS_DEPLOY_ROLE_ARN }}"
    assert "Check that the API answers" in steps


def test_the_container_check_runs_on_pull_requests_that_could_break_the_image():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-build.yml").read_text(encoding="utf-8")
    )
    trigger = workflow[True] if True in workflow else workflow["on"]
    paths = set(trigger["pull_request"]["paths"])
    assert {"infra/**", "src/**", "pyproject.toml"} <= paths


def test_the_deploy_role_trusts_only_this_repository_on_the_main_branch():
    role = load("github-deploy-role.yaml")
    trust = role["Resources"]["DeployRole"]["Properties"]["AssumeRolePolicyDocument"][
        "Statement"
    ]
    (statement,) = trust
    condition = statement["Condition"]["StringEquals"]
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity" and "*" not in str(
        statement["Principal"]
    )
    assert condition["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    subjects = [
        entry["Fn::Sub"]
        for entry in condition["token.actions.githubusercontent.com:sub"]
    ]
    # GitHub presents the identity with permanent ids (repo:ORG@ID/REPO@ID:...); the plain form is also accepted
    assert subjects == [
        "repo:${GitHubOrg}@${GitHubOrgId}/${GitHubRepo}@${GitHubRepoId}:ref:refs/heads/${Branch}",
        "repo:${GitHubOrg}/${GitHubRepo}:ref:refs/heads/${Branch}",
    ]
    assert (
        "StringLike" not in statement["Condition"]
    )  # no wildcards anywhere in the trust rule
    params = role["Parameters"]
    assert (
        params["GitHubOrg"]["Default"] == "Othello-HackHive"
        and params["GitHubRepo"]["Default"] == "Othello"
    )
    assert params["Branch"]["Default"] == "main" and "DeployRoleArn" in role["Outputs"]


def test_the_ids_in_the_trust_rule_are_the_ones_github_really_sent():
    """Copied from the identity printed by a real run (see the 'Show the identity' step of the Deploy workflow)."""
    params = load("github-deploy-role.yaml")["Parameters"]
    assert (
        params["GitHubOrgId"]["Default"] == "343091625"
        and params["GitHubRepoId"]["Default"] == "1420966048"
    )


def test_the_deploy_role_has_no_blanket_permissions():
    role = load("github-deploy-role.yaml")
    policy = role["Resources"]["DeployRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    for statement in policy:
        actions = (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
        assert (
            "*" not in actions and "iam:*" not in actions and "sts:*" not in actions
        ), statement["Sid"]
        if (
            statement["Resource"] == "*"
        ):  # only lookups, or services that cannot be limited by name
            assert statement["Sid"] in {"ReadOnlyLookups", "AppResources"}
    iam = next(s for s in policy if s["Sid"] == "RolesOfTheStackOnly")
    assert all(
        "role/${StackName}-" in str(r) or "role/aws-sam-cli-managed-default-" in str(r)
        for r in iam["Resource"]
    )


# --------------------------------------------------- the deployment guide stays true


def deployment_doc():
    return (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")


def test_the_guide_names_every_template_parameter_and_every_github_setting_the_workflow_uses(
    template,
):
    text = deployment_doc()
    assert [name for name in template["Parameters"] if f"`{name}`" not in text] == []
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"(?:vars|secrets)\.([A-Z_]+)", workflow))
    assert {
        "AWS_DEPLOY_ROLE_ARN",
        "ALLOWED_ORIGINS",
        "OTHELLO_API_KEY",
        "WORKER_CONCURRENCY",
        "AUTO_DEPLOY",
    } <= used
    assert [name for name in used if f"`{name}`" not in text] == []


def test_the_guide_uses_the_real_names_of_the_stack_region_and_log_groups():
    text = deployment_doc()
    for needle in (
        "us-east-1",
        "othello-cable-ring",
        "/aws/lambda/othello-cable-ring-worker",
        "/aws/lambda/othello-cable-ring-api",
    ):
        assert needle in text, needle
    assert (
        "infra/github-deploy-role.yaml" in text
        and "infra/template.yaml" in text
        and "tests/data/uploads/one_room.json" in text
    )


def test_the_guide_is_linked_from_the_docs_index():
    assert "DEPLOYMENT.md" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")


def test_the_identity_debug_step_runs_only_by_hand_and_never_prints_the_token():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["deploy"]["steps"]
    names = [step.get("name", "") for step in steps]
    debug = steps[
        next(i for i, name in enumerate(names) if name.startswith("Show the identity"))
    ]
    sign_in = next(
        i for i, name in enumerate(names) if name.startswith("Sign in to AWS")
    )
    assert (
        debug["if"] == "github.event_name == 'workflow_dispatch'"
        and names.index(debug["name"]) < sign_in
    )
    script = debug["run"]
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in script and "claims.get(key)" in script
    assert (
        "print(token" not in script
        and 'echo "$reply"' not in script
        and 'echo "$token"' not in script
    )


def test_the_deploy_step_never_hands_the_sam_tool_an_empty_setting():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    )
    script = next(
        s for s in workflow["jobs"]["deploy"]["steps"] if s.get("name") == "Deploy"
    )["run"]
    # the two settings that always have a value are given a default; the key is added only when it is set
    assert "${ALLOWED_ORIGINS:-" in script and "${WORKER_CONCURRENCY:-0}" in script
    assert (
        'if [ -n "${OTHELLO_API_KEY}" ]; then' in script
        and 'overrides+=("ApiKey=${OTHELLO_API_KEY}")' in script
    )
    first_use = script.index("ApiKey=${OTHELLO_API_KEY}")
    assert script.rindex("if [ -n", 0, first_use) > script.index(
        "overrides=("
    )  # only inside the "if"
    assert '--parameter-overrides "${overrides[@]}"' in script


def test_the_deploy_role_may_look_at_the_helper_stack_sam_creates_for_the_image_repositories():
    """Found by the first deploy: AccessDenied on stack/othello-cable-ring-<hash>-CompanionStack."""
    role = load("github-deploy-role.yaml")
    statements = role["Resources"]["DeployRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    stacks = next(s for s in statements if s["Sid"] == "Stacks")["Resource"]
    patterns = [r["Fn::Sub"] for r in stacks]
    assert any(p.endswith(":stack/${StackName}-*/*") for p in patterns)
    assert not any(
        p.endswith((":stack/*", ":stack/*/*")) for p in patterns
    )  # still only our own stacks


def test_the_guide_puts_the_deploy_from_your_own_computer_first_and_explains_the_optional_switch():
    text = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert text.index("sam build --template-file infra/template.yaml") < text.index(
        "One-time setup for Way 2"
    )
    assert "`AUTO_DEPLOY`" in text and "AllowedOrigins=" in text


def test_the_deploy_role_may_push_to_the_dash_less_image_repositories_sam_creates():
    """SAM names them othellocablering<hash>/<function>repo: seen in the real deploy output."""
    role = load("github-deploy-role.yaml")
    statements = role["Resources"]["DeployRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    images = next(s for s in statements if s["Sid"] == "ImageRepositories")
    assert images["Action"] == "ecr:*"
    text = str(images["Resource"])
    assert (
        "Compact" in text and "Fn::Split" in text and "repository/${Compact}*" in text
    )
    assert "repository/${StackName}*" in text and "aws-sam-cli-managed-default" in text
    assert "repository/*" not in text.replace(
        "repository/${", ""
    )  # never every repository in the account
