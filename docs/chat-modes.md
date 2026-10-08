# Chat response modes

EVA exposes two explicit response modes so the conversation UI and the model
adapter share one request contract:

| Mode | UI model | Request behavior |
| --- | --- | --- |
| `normal` | 2×2×2 cube | concise answer policy; native reasoning disabled or low |
| `deep` | 3×3×3 cube | analysis/checking policy; native reasoning enabled when supported |

The browser snapshots the selected mode at submit time. It sends `mode` in the
streaming, asynchronous, and synchronous chat request bodies, stores it on the
user message, and starts the matching cube. A later selection is queued for the
next message. This avoids changing an already admitted request while its reply is
being delivered.

The assistant message also keeps its own delivery status and request ID. Full
answer copy returns the received Markdown text, including fenced code, without
button or tool-card labels. Clipboard failures offer a manual-copy hint. Streaming
updates preserve the reader's scroll position and focus on message controls;
terminal announcements use a separate live region instead of re-announcing the
whole transcript on every token.

An explicit `accepted: false` response is shown as not executed. A failed terminal
receipt permits an explicit **Send again** action. This creates a new request using
the original message's mode and transport; it preserves the composer draft and
does not repeat the original long-term-memory consent. It does not alter the mode
selected for the next message.

An interrupted stream, exhausted delivery polling, or a missing receipt is shown
as **Outcome unknown**, not as an execution failure. Partial text is preserved.
The `X-EVA-Task-ID` stream header or asynchronous acknowledgement provides the ID
for **Check result**, which only reads `/api/chat/result/{task_id}`. A transport
failure before an ID is received cannot be queried; the UI still avoids automatic
resubmission. IDs remain associated with their original messages after local
waiting ends, allowing a late WebSocket reply to update the correct message.
Neither a query nor an old reply can settle a newer response's cube animation.
An uncertain execution state takes precedence over an apparent failed receipt.

The UI does not offer server cancellation. Disconnecting local stream delivery
leaves an admitted server request potentially running. These request IDs and
message associations live only in the current page; reloading does not persist
the transcript or rehydrate receipt queries. Evidence and current verification
limits are recorded in [CHAT-01 acceptance](ui-logo-chat-acceptance.md).

If capability discovery is unavailable, the controls remain usable and the
composer says that the selected mode will still be sent. It does not guess that
the provider supports native reasoning.

The server repeats the validation after admission. `GET /api/chat/modes` exposes
the selected provider's strategy without returning credentials. Receipts include
`mode` and `mode_info`; the chat transcript renders the public strategy beside
the assistant reply, so users can distinguish `native`, `prompt`, `mock`, and
preview fixtures without seeing private reasoning content.

Native mappings are deliberately conservative. OpenAI auto-detection is limited
to GPT-5.4/GPT-5.5 model names and sends `reasoning_effort`; DeepSeek sends its
`thinking` flag and omits temperature for deep requests; Claude Sonnet/Opus 4.6
uses adaptive thinking and effort. Other models use the mode prompt policy unless
`EVA_LLM_REASONING_STYLE` explicitly selects a compatible adapter path. The deep
token and timeout ceilings are controlled by `EVA_DEEP_MAX_TOKENS` and
`EVA_DEEP_TIMEOUT`.

The adapter only returns public text blocks. Private reasoning content is never
placed in SSE frames, WebSocket receipts, persisted replies, or the UI. These
provider mappings follow the current vendor contracts:

- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
- [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Claude extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)
