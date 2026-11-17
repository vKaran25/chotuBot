# chotuBot chat evaluation

The runner sends human-approved cases through a running local chotuBot app. It reads the provider and model from the app's public Settings endpoint. Configure NIM and select the model in chotuBot before running a live baseline. The runner never reads the NVIDIA key.

## First live baseline

Dataset version `0.2.0` ran 39 approved cases against `nvidia/nemotron-3.5-lightning-30b-a3b`. All 52 chat submissions completed without provider errors or rate-limit responses.

| Check | Passed | Total |
| --- | ---: | ---: |
| Combined case verdict | 36 | 39 |
| Final answer rubric, assessed by Codex | 37 | 39 |
| Required tool checks | 11 | 12 |

`project-004` recovered from an invalid `project: "none"` call. `project-009` missed the lookup for Priya's project. `notes-007` omitted that the recorded budget amount was monthly. The invalid `project: "none"` call appeared 11 times across 10 cases.

## Run and review

From the repository root in PowerShell, use a new report path for each baseline or regression run.

```powershell
& .\.venv\Scripts\python.exe .\eval-datasets\chat-answering\run.py --live --out .\data\evals\baseline.json
```

Set a larger cooldown with `--interval 15` when sharing the NIM quota. The default waits eight seconds after each chat response and sends one request at a time. Each chat can make multiple provider calls. The runner honors `Retry-After` when chotuBot exposes it and otherwise backs off before retrying a rate limit or a documented provider failure. Transport timeouts stop the run because the server might already have saved the exchange.

The live run creates real chat and usage records. To keep them separate from personal chats, start the local app on a separate port with a fresh `CHOTU_DB_PATH` and pass that port with `--base-url`. Use one runner process per report. A stopped case restarts in a fresh conversation, and completed cases are retained.

```powershell
& .\.venv\Scripts\python.exe .\eval-datasets\chat-answering\run.py --live --resume --out .\data\evals\baseline.json
```

The JSON report pins the dataset and fixture hashes and records the provider, model, replies, and tool activity. It checks labelled tool calls automatically. Grade each final answer against its stored rubric by changing `answer_grade` from `pending` to `pass` or `fail`. Summarize that file without making requests.

```powershell
& .\.venv\Scripts\python.exe .\eval-datasets\chat-answering\run.py --summary --out .\data\evals\baseline.json
```

Use the same approved dataset and fixture hashes for regressions and compare cases by ID. Exit code `1` means a tool check or recorded human grade failed. Exit code `2` means the run is incomplete or hit an operational error. Exit code `0` means collection and the available checks finished successfully. The summary reports any answer grades that still need review.

For an offline plumbing check, run against an app configured for the demo provider, omit `--live`, and pass `--interval 0`. Demo output can fail the target-model rubric. Run the mocked runner tests with `python -m pytest tests/unit/test_golden_runner.py -q`.
