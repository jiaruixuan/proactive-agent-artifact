### Tracker Payload Fields

| Field | Meaning | Notes |
| --- | --- | --- |
| `tracker_type` | Tracker category | `click`, `jsError`, `resourceError`, or `promiseError` |
| `tracker_event_type` | Event name | Application-defined event type |
| `tracker_event_options` | Event options | Additional event payload |
| `tracker_err_msg` | Error message | Used by error trackers |
| `tracker_time` | Event timestamp | Unix timestamp in milliseconds |
| `tracker_error` | Runtime error details | Used by JavaScript runtime errors |
| `tracker_source` | Error source | Script or resource source |
| `tracker_lineno` | Error line | Source line number |
| `tracker_colno` | Error column | Source column number |
| `ft_token` | User identifier | Treat as sensitive and remove from public logs |

### Local Tracker Storage Fields

The local tracker cache stores aggregated IDE-assistant usage metrics, including:

1. `accept_line_week`: accepted completions in the last seven days.
2. `accept_line_total`: total accepted completions.
3. `accept_days`: number of days with accepted completions.
4. `accept_rate`: `accept_cnt / insert_without_paste_cnt`.
5. `code_retention`: retained generated code ratio.
6. `right_click_cnt`: right-click feature usage count.
7. `accept_cnt`: accepted character count.
8. `accept_cnt_total`: total accepted character count.
9. `completion_language`: completion language.
