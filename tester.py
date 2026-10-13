import boto3
import yaml
import json
import time
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
AWS_PROFILE   = "fury-admin"
AWS_REGION    = "us-east-1"
MODEL_ID      = "us.meta.llama3-3-70b-instruct-v1:0"
MAX_TOKENS    = 512
DELAY_SECONDS = 5
MAX_TESTS     = None

# ── BEDROCK CLIENT ────────────────────────────────────────────────────────────
def get_client():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return session.client("bedrock-runtime")

# ── INVOKE MODEL ──────────────────────────────────────────────────────────────
def invoke(client, prompt):
    try:
        response = client.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0.0}
        )
        text  = response["output"]["message"]["content"][0]["text"]
        usage = response.get("usage", {})
        return {"success": True, "text": text, "usage": usage}
    except client.exceptions.ThrottlingException:
        import time as _t
        print("       [throttled — waiting 10s]")
        _t.sleep(10)
        try:
            response = client.converse(
                modelId=MODEL_ID,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0.0}
            )
            text  = response["output"]["message"]["content"][0]["text"]
            usage = response.get("usage", {})
            return {"success": True, "text": text, "usage": usage}
        except Exception as e2:
            return {"success": False, "text": "", "error": f"THROTTLED_RETRY_FAILED: {e2}"}
    except Exception as e:
        return {"success": False, "text": "", "error": str(e)}

# ── EVALUATE ──────────────────────────────────────────────────────────────────
def evaluate(response_text, test):
    text_lower   = response_text.lower()
    fail_keywords = [k.lower() for k in test.get("fail_keywords", [])]
    pass_keywords = [k.lower() for k in test.get("pass_keywords", [])]
    failed_on = [k for k in fail_keywords if k in text_lower]
    passed_on = [k for k in pass_keywords if k in text_lower]
    if failed_on:
        return "FAIL"
    elif passed_on:
        return "PASS"
    else:
        return "SUSPICIOUS"

# ── LOAD TESTS ────────────────────────────────────────────────────────────────
def load_tests(path):
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "tests" in data:
        return data["tests"]
    else:
        raise ValueError(f"Unexpected YAML format in {path}")

# ── COLOR ─────────────────────────────────────────────────────────────────────
COLORS = {
    "PASS":       "\033[92m",
    "FAIL":       "\033[91m",
    "SUSPICIOUS": "\033[93m",
    "ERROR":      "\033[91m"
}
RESET = "\033[0m"

def color(text, outcome):
    return f"{COLORS.get(outcome, '')}{text}{RESET}"

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    tests  = load_tests("test_cases/attacks.yaml")
    client = get_client()

    if MAX_TESTS:
        tests = tests[:MAX_TESTS]

    total   = len(tests)
    results = []
    counts  = {"PASS": 0, "FAIL": 0, "SUSPICIOUS": 0, "ERROR": 0}

    print(f"\n{'='*60}")
    print(f"  LLM Prompt Injection Tester")
    print(f"  Model  : {MODEL_ID}")
    print(f"  Region : {AWS_REGION}")
    print(f"  Tests  : {total}")
    print(f"{'='*60}\n")

    for i, test in enumerate(tests, 1):
        tid      = test.get("id", f"T{i:03d}")
        category = test.get("category", "unknown")
        name     = test.get("name", "Unnamed")
        severity = test.get("severity", "MEDIUM")
        prompt   = test.get("input", "")

        print(f"[{i:02d}/{total}] {tid} | {category} | {name} | {severity}")

        response = invoke(client, prompt)

        if not response["success"]:
            outcome = "ERROR"
            note    = response.get("error", "Unknown error")
            print(f"       -> {color('ERROR', 'ERROR')} -- {note}\n")
        else:
            outcome = evaluate(response["text"], test)
            snippet = response["text"][:120].replace("\n", " ")
            print(f"       -> {color(outcome, outcome)}")
            print(f"       Response: {snippet}...\n")

        counts[outcome] += 1

        results.append({
            "id":        tid,
            "category":  category,
            "name":      name,
            "severity":  severity,
            "outcome":   outcome,
            "prompt":    prompt,
            "response":  response.get("text", ""),
            "error":     response.get("error", ""),
            "usage":     response.get("usage", {})
        })

        if i < total:
            time.sleep(DELAY_SECONDS)

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    n_pass = counts["PASS"]
    n_susp = counts["SUSPICIOUS"]
    n_fail = counts["FAIL"]
    n_err  = counts["ERROR"]

    pass_line = f"PASS       : {n_pass:3d}  ({n_pass/total*100:.0f}%)"
    susp_line = f"SUSPICIOUS : {n_susp:3d}  ({n_susp/total*100:.0f}%)"
    fail_line = f"FAIL       : {n_fail:3d}  ({n_fail/total*100:.0f}%)"
    err_line  = f"ERROR      : {n_err:3d}  ({n_err/total*100:.0f}%)"

    print(f"\n{'='*60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"  {color(pass_line, 'PASS')}")
    print(f"  {color(susp_line, 'SUSPICIOUS')}")
    print(f"  {color(fail_line, 'FAIL')}")
    print(f"  {err_line}")
    print(f"{'='*60}\n")

    # ── FINDINGS ──────────────────────────────────────────────────────────────
    findings = [r for r in results if r["outcome"] in ("FAIL", "SUSPICIOUS")]
    if findings:
        print(f"  WARNING: FINDINGS REQUIRING ATTENTION ({len(findings)})")
        print(f"  {'-'*50}")
        for f in findings:
            print(f"  {color(f['outcome'], f['outcome'])} | {f['id']} | {f['severity']} | {f['name']}")
        print()

    # ── SAVE REPORT ───────────────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"results_{timestamp}.json"

    report = {
        "scan_time":   datetime.now().isoformat(),
        "model_id":    MODEL_ID,
        "region":      AWS_REGION,
        "total":       total,
        "summary":     counts,
        "findings":    findings,
        "all_results": results
    }

    with open(filename, "w") as f:
        json.dump(report, f, indent=2)

    print(f"  Report saved -> {filename}\n")

if __name__ == "__main__":
    main()
