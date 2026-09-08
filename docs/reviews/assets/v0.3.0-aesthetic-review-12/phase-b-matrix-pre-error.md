# haochen v0.3.0 Aesthetic Review 12 — Phase B Matrix (Valid Environment)

- Candidate: /private/tmp/haochen-round12-candidate.2griVw/haochen.app
- HAOCHEN_HOME: /private/tmp/haochen-qa-round12-valid.S0lEB4
- App PID: 34133
- Engine PID: 34144
- Date: 2026-09-09 (Asia/Shanghai)

| Check | Result | Direct user observation |
|---|---|---|
| Long full-chat request, stop after body appears | PASS | Requested ≥1200 Chinese characters in ten numbered sections. After visible body reached section 9, clicked red Stop. Current viewport ended with amber “已停止生成 · 上述内容未完成”; the partial text remained a bordered incomplete card, never relabeled as a conclusion. |
| Stop semantics after close/reopen | PASS | Closed the chat window and reopened with ⌘1. It returned to the same section-9 fragment and the same amber incomplete marker. |
| Continuation grounded at exact cutoff | PASS | Asked “继续，用一句话告诉我刚才停在哪里”. Answer: “停在‘9. 周末野餐的一桌人’那段，正说到‘就能‘拥有’’，后面被截断了。” This matched the visible final fragment, not an earlier subject. User bubble showed only the typed sentence; no internal context tags appeared. |
| Three distinct first-turn topics via menu-bar New Session | PASS | Completed pet-bubble first turns about blue-whale krill intake, tomato-and-egg cooking order, and locating Jupiter. Sidebar titles were “蓝鲸一天大约吃多少磷虾？”, “番茄炒蛋先炒蛋还是先炒番茄？”, and “今晚在城市里怎么看到木星？”. They were readily distinguishable and contained no odd colon or technical prefix. |
| ⌘1 | PASS | From the pet/transient bubble, ⌘1 opened the full conversation window. |
| ⌘N | PASS | In full chat, ⌘N created and selected a fresh “新会话” without losing prior sessions. |
| ⌘, | FAIL | With full chat active and its empty input focused, pressing ⌘, twice caused no visible change and kept focus in the chat input. Menu-bar “设置…” opened the settings window immediately, proving the destination was available but the advertised shortcut path was not. |
| Pet right-click “打开完整对话” | FAIL | Right-clicking the visible pet at its center, then right-clicking its accessible image, then repeating with the alternate right-button form produced no context menu. Only the character pose/hover changed. Therefore “打开完整对话” was absent/unreachable and could not be exercised. |
| Small-window short answer | PASS | “17 加 25 等于多少？” returned “17 + 25 = 42。” in a compact bubble with reachable “继续问” and “查看详情”. |
| Temporary result departure | PASS (observed, not used as a timing precision claim) | The completed arithmetic result was allowed to sit; an outer CUA elapsed interval of 22,001 ms later the bubble had returned to the pet. No issue was filed merely for automatic departure. |
| Continue after temporary departure and View Details | PASS | Reopened the pet, asked “怎么算的？用一句话说。”, received a correct one-sentence carry explanation, and opened View Details. Both turns and detail cards were intact and readable. |
| Input / processing / result visuals | PASS | Input kept clear hint and green send action; processing showed human status copy plus Stop; result used readable cream card, restrained spacing, muted secondary and green primary actions. No crop/collision found. |
| Maximize / restore | PASS | Zoomed full chat to screen size and restored it using the green-window zoom action. Sidebar, input, send button and session content remained reachable. |
| Top / bottom / left / right placement | PASS | Top edge opened bubble downward with its tail on top; bottom edge opened upward with tail below; left and right edges opened inward. Text field and send button stayed fully visible. |
| Refuse screen reading | PASS | An explicit screen-content question produced the clear consent card with “读吧” and “不读”. Choosing “不读” returned to the pet; no screen content was exposed and full history retained a plain-language explanation. |
| Settings visual / no credential exposure | PASS | Menu-opened settings showed provider/model, masked secure field placeholder, and “已验证 · 安全存储”; no credential value was visible. |
| New dead ends, crop, imbalance, unreachable controls, session mix-up, inhuman errors | FAIL due to the two interaction dead ends above | No additional content crop, button reachability, session mix-up, or wording defect appeared in the valid fixture. The broken ⌘, path and missing pet context menu are direct interaction dead ends. |

## Valid-environment disposition before error fixture

NOT PASS. Core chat, interruption persistence, continuation grounding, titles, transient results, detail view, resizing, placement and privacy refusal are good. However, this gate explicitly requires every prescribed route to work. The nonfunctional ⌘, shortcut and absent/unreachable pet right-click menu are reproducible user-facing failures, so the candidate cannot pass without further correction and retest.
