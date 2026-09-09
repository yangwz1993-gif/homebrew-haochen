# Haochen v0.3.0 Round 14 — Action results

Candidate: `/private/tmp/haochen-round14-candidate.OKqRhA/haochen.app`

DMG SHA-256 verified: `c9816f469159b23ce5fa72a62c355a6893d7e2d21ebc9e78b3cde1e9d52e221e`

## Priority hard gates

### 1. Avatar click and `⌘1`

- PASS — With the avatar in the foreground, one click opened the compact composer.
- PASS — Escape closed the composer. From the foreground avatar, `⌘1` opened the complete conversation window.
- PASS — Pressing `⌘1` from the compact composer opened the complete conversation; pressing it again inside the complete conversation kept the window usable.
- Observation, not a product failure: immediately after closing Settings, the first avatar click only focused the app and the second opened the composer. This did not reproduce once the avatar was explicitly foregrounded.

### 2. Avatar context-menu entry

- PASS — App screenshots were cropped to the 96×96 avatar and did not show the native menu.
- PASS by action/result — Right-click avatar, then `Down`, `Down`, `Enter` opened the complete conversation.
- PASS after restart — The candidate and its engine were terminated by exact PID, the same absolute candidate path was restarted, and the same keyboard selection again opened the complete conversation.

### 3. Markdown rendering

- PASS — Prompt: `请只用 Markdown 粗体回答：巴黎`. Compact result showed only `巴黎`; no `**`, backticks, or other format-control characters.
- PASS — Detail view displayed a readable conclusion card with the answer.
- PASS — Natural prompt: `第一次去东京旅行，请推荐三个最值得去的地点，并用一句话说明各自特色。` Compact result showed clean prose without format-control characters. Detail view rendered `浅草寺`、`涩谷`、`东京晴空塔` as bold list-item names and remained readable.

## Regression checks

- PASS — Brief answer, detail view, and contextual follow-up all completed. Follow-up `这三个里最适合晚上去的是哪个？一句话回答。` correctly answered in the context of the three recommended locations.
- PASS — A long eight-section Tokyo article was stopped while body text was visible and the send control still read `停止生成`. The current view changed to `已停止生成 · 上述内容未完成`; after collapsing and reopening via `⌘1`, the same incomplete-content semantics remained.
- PASS — Two distinct topic sessions received distinct sidebar titles: `用一句话解释光合作用。` and truncated `给我三个零基础学习吉他的建...`.
- PASS — `⌘,` opened Settings.
- PARTIAL / TOOL-LIMITED — Clicking the green window control visibly maximized the conversation to fill the capture. CUA no longer exposed the native window controls in that state; `Ctrl+⌘F`, Escape, and a title-region double-click did not restore it. This is not treated as a product failure without independent native-window confirmation.
- PASS — A screen-reading request produced a clear consent card. Clicking `不读` yielded `read_screen 未执行 · 未获授权`, followed by an answer stating that no screen content was read.

## Restart/history finding

- FAIL / HIGH RISK — Session history did not survive two exact process restarts. Reproduction: create completed conversations and observe distinct sidebar titles; terminate only the candidate PID and its candidate-owned engine PID with SIGTERM; restart through the candidate absolute path; press `⌘1`. The sidebar showed only `新会话` and no prior titles/content. This reproduced once after the initial short conversation and again after several completed sessions, including the stopped long-answer session.
- Scope note: the restart was process-level SIGTERM rather than choosing the app-menu Quit item. The result is nevertheless repeatable and materially risks losing conversation history on crash/update/normal process termination.

## No-credential fixture

- UNRESOLVED / TOOL ENVIRONMENT LIMITATION — `launchctl getenv HAOCHEN_HOME` showed the requested no-credential fixture, but a fresh process launched by the persistent CUA service still spawned its engine with the valid fixture path visible in the read-only process command line. The resulting successful reply therefore cannot be attributed to the no-credential fixture.
- The no-credential Chinese error, three Retry counts, Settings action, and complete error history are not adjudicated. No key was viewed, entered, copied, or logged.

## Aesthetic observations affecting release quality

- The cream/green rounded-card system and pixel character are coherent and memorable.
- The avatar has no visible click hint or next-step cue, so the first action depends on guessing.
- The default complete-conversation view has a disproportionately large empty center for short conversations and feels unfinished.
- The detail title `当前会话详情` is generic rather than topic-identifying.
- The compact composer uses a labelled `发送` button, while the full composer uses a very small icon-only send control.
- Settings is visually coherent but narrow, vertically clipped at the bottom, text-dense, and relatively low-contrast. Escape did not close it during two checks.

