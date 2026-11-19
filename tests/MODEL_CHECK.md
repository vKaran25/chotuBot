# Resume model check

Tested on 20 November 2026 through the running app's public HTTP API, using NVIDIA NIM and Karan_Verma_Resume-1.pdf. The original model selection was restored after testing.

## Method

Each of the 13 configured models received the same resume question in a fresh conversation. Each received two attempts. A second focused question was sent to the six models that returned nonempty, cited resume answers. All six answered the focused question correctly against the PDF.

Summary question

> Summarize Karan Verma's resume. Name his three projects, briefly explain what each does, and list his core technical skills. Use only the attached resume, cite the source, and keep the answer under 250 words.

Focused question

> According to Karan Verma's resume, what does BrandGate do, and how does it isolate company data? Answer in under 100 words using only the resume and cite the source.

The initial automatic check expected all three project names in the summary. The retrieved excerpts omitted BrandGate's full section, so that check failed even for models returning answers. It was not used to remove models. The focused question retrieved the relevant section and checked the answer against BrandGate's description and PostgreSQL row-level security in the PDF.

## Results

Times are end-to-end HTTP durations for this run, not model speed benchmarks.

| Model | Category | Summary attempts | Focused answer | Decision |
| --- | --- | --- | --- | --- |
| Nemotron 3.5 Lightning 30B-A3B | Fast | HTTP 200 in 5.3s; HTTP 200 in 4.8s | Correct, cited, HTTP 200 in 7.4s | Keep |
| Nemotron 3.5 Bolt E4B | Fast | HTTP 200 in 3.4s; HTTP 200 in 3.2s | Correct, cited, HTTP 200 in 1.4s | Keep |
| Laguna XS 2.1 | Fast | HTTP 200 in 2.4s; HTTP 200 in 10.4s | Correct, cited, HTTP 200 in 1.3s | Keep |
| MiniMax M3.1 Flash | Fast | HTTP 502 in 47.3s; HTTP 502 in 33.1s | Not run | Remove |
| GLM 5.3 Flash | Fast | HTTP 503 in 60.5s; HTTP 503 in 60.6s | Not run | Remove |
| Nemotron 3.5 Super VL 120B-A12B (early access) | Thinking | HTTP 200 in 10.3s; HTTP 200 in 11.8s | Correct, cited, HTTP 200 in 2.2s | Keep |
| Nemotron 3 Nano Omni 30B-A3B Reasoning | Thinking | HTTP 502 in 7.1s; HTTP 502 in 7.6s | Not run | Remove |
| Muse Glimmer 30B | Thinking | HTTP 503 in 60.2s; HTTP 503 in 60.3s | Not run | Remove |
| Gemma 4 31B IT | Thinking | HTTP 503 in 60.3s; HTTP 503 in 60.3s | Not run | Remove |
| Nemotron 3 Ultra 550B-A55B | Flagship | HTTP 200 in 9.1s; HTTP 200 in 18.1s | Correct, cited, HTTP 200 in 6.0s | Keep |
| Kimi K3.1 Pro | Flagship | HTTP 503 in 60.5s; HTTP 503 in 60.5s | Not run | Remove |
| Mistral Large 4 | Flagship | HTTP 503 in 60.5s; HTTP 503 in 60.5s | Not run | Remove |
| Step 5 Preview | Flagship | HTTP 200 in 55.4s; HTTP 200 in 31.9s | Correct, cited, HTTP 200 in 5.2s | Keep |

Provider failures returned HTTP 503 with provider_unavailable. Empty answers returned HTTP 502 with malformed_model_output. Each removed model failed twice in this application and account during this run. These outcomes do not establish permanent model unavailability.

## Example verified answer

> BrandGate is an AI brand content review tool that helps teams check emails, posts, and documents against company rules before publication. It explains issues using approved references and suggests corrections for human review. Company data is isolated using PostgreSQL row-level security, along with audit history and text redaction [1].

Returned by Laguna XS 2.1 in 1.3 seconds for the focused question.

## Limits

This verifies one PDF and two prompts. Broad summaries omitted a project or treated the calendar planner as a separate project. The focused answers demonstrate source-grounded responses, not complete resume summarization or general reasoning quality. Some answers were visibly truncated. The retained models can still fail or vary on later requests.

Configuration was reduced to three Fast models, one Thinking model, and two Flagship models. Test chats and the uploaded PDF remain in the app for inspection.
