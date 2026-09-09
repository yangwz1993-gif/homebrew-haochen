# Haochen v0.3.0 Round 14 — Evidence index

## Persisted evidence

- `blind-observations-sealed.md` — blind first-use observations, sealed before hard-gate execution.
- `blind-observations-sealed.sha256` — seal hash: `417f6dd29a7d73926a9493554aa37f87b73c1f8a157d44af17947afc630d610a`.
- `action-results.md` — action-by-action results and exact reproduction notes.
- `../v0.3.0-aesthetic-review-14.md` — final verdict.

## CUA visual observations captured in-session

1. Initial 96×96 avatar: pixel character only; no visible usage hint.
2. Compact composer: cream speech bubble, placeholder with keyboard shortcut, labelled green send button.
3. Compact completed answer: clean summary with `继续问` and primary `查看详情` buttons.
4. Full detail: right-aligned user bubble, `结论` and `依据与细节` cards, large unused center area.
5. Settings: provider/model/key/behavior cards; verified state visible; bottom content clipped at default size.
6. `⌘1` from avatar and compact composer: full conversation reached; repeat inside full window remained usable.
7. Right-click action: menu itself outside cropped App capture; `Down`, `Down`, `Enter` result visibly reached full conversation both before and after restart.
8. Markdown explicit case: compact `巴黎` without control characters; readable detail card.
9. Markdown natural case: compact Tokyo recommendation without control characters; detail bullet list with visibly bold place names.
10. Long-answer interruption: body visible while `停止生成`; after stop and reopen, amber `已停止生成 · 上述内容未完成` remained.
11. Distinct titles: science and guitar titles simultaneously visible in the sidebar before restart.
12. Read-screen denial: consent card with `不读` / `读吧`; refusal state `未执行 · 未获授权`; explanatory final response.
13. Post-restart full conversation: only `新会话` visible, prior session titles absent; reproduced twice.

The CUA API emitted screenshots into the live review session but did not expose a supported filesystem-save operation. Therefore the durable evidence is the sealed observation, action transcript, and exact textual/visual-state index above; no shell screenshot substitute was used.

## Tool limitations

- Native context menu was outside the App capture; adjudication used the required keyboard action and destination result.
- Maximized native controls were absent from CUA accessibility state, preventing a reliable restore verdict.
- The persistent CUA launch service retained the valid `HAOCHEN_HOME` environment despite the updated launchctl value, preventing an authentic no-credential run.
