# v0.3.0 aesthetic review 16 — action results

- Candidate: `/private/tmp/haochen-round16-final.FIeSfB/haochen.app`
- DMG SHA-256: `98c0ef5b87ad06fbe6e40d99b891c73f309bf8937e283960e5b73ddd67dbb1e4` (matched expected)
- Display calibration: CGEvent logical space `1470×956`; full-screen capture `2940×1912` pixels; scale `2×`.
- Candidate process evidence: main executable under the candidate bundle; child engine under `Contents/Resources/engine/haochen-engine`; both used the selected independent `HAOCHEN_HOME`.

## Exact action/result record

1. Empty-home path A2: launched the exact candidate, closed the welcome window at ~550 ms with CGEvent, then waited ~2 s without clicking the pet. Result: a short readable pet-side hint appeared: “点一下直接问我 · 右键打开完整对话和设置”. Exact marker `interaction-hint-v1` was absent before close, absent while the hint was visible, and absent after timeout. Evidence: `16-emptyA2-2s-after-fast-close.png`, `17-emptyA2-after-timeout.png`.
2. Empty-home path B: launched with `HAOCHEN_SKIP_ONBOARDING=1` and `HAOCHEN_SKIP_PERMISSION_GUIDE=1`; checked `interaction-hint-v1` at ~550 ms; posted a real left click to the pet before 900 ms. Result: marker absent before click, present after click; input bubble and usage explanation appeared together. Evidence: `18-emptyB-before900ms-left-click.png`.
3. Posted a real macOS right-click CGEvent to the pet. Result: stable native menu with “打开完整对话 / 新会话 / 设置… / 退出”; selected “打开完整对话” by screen coordinate and reached the complete conversation window. Evidence: `04-real-right-click-menu.png`, `05-menu-open-full-conversation.png`.
4. Valid historical fixture: copied to an independent temp home without reading or outputting credential values. Result: multiple titles, selected content, Markdown rendering, denied-screen-read semantics, and current selection survived two cold launches. No ghost “新会话” item appeared. Evidence: `19-valid-fixture-cold-start-cmd1.png`, `20-valid-fixture-click-history-item.png`, `21-valid-fixture-second-cold-restart.png`.
5. Short-thread layout: selected the “你好” thread by real screen-coordinate click. Result: the user message and answer begin at the top of the content area in natural reading order, with no hundreds-of-pixels spacer above. Evidence: `20-valid-fixture-click-history-item.png`, `21-valid-fixture-second-cold-restart.png`.
6. Small-bubble answer: sent “请只用一句中文介绍你自己。” with the valid fixture. Result: one concise human-sounding conclusion plus “继续问 / 查看详情”. Evidence: `23-valid-short-bubble-answer.png`.
7. No-credential path: sent two real prompts in one home, observed two explicit Chinese API-key errors directing the user to settings, then cold-restarted and observed both failed turns preserved. Repeated with the supplied no-credential fixture and confirmed its error turn survived restart. Evidence: `06-no-credentials-first-error.png`, `07-no-credentials-second-error.png`, `08-no-credentials-restart-cmd1.png`, `26-nocred-fixture-error.png`, `27-nocred-fixture-error-after-restart.png`.
8. Shortcut/smoke: CGEvent Command-1 opened the full chat on cold launch; Settings opened from the right-click menu and Escape closed it; fixture rendered Markdown bold without exposing `**`; denied screen-reading remained visibly “未执行 · 未获授权” rather than success. Evidence: `08-no-credentials-restart-cmd1.png`, `09-settings-open.png`, `10-settings-esc-closed.png`, `19-valid-fixture-cold-start-cmd1.png`.

## Calibration correction

Early exploratory captures `01`–`03` predated the clarified acceptance split and checked `onboarding-state.json`. That file belongs to the welcome flow and is not the interaction marker. Final judgment uses only exact marker `interaction-hint-v1` and the calibrated A2/B paths above.
