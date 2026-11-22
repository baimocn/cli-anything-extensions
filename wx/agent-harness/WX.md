# WX.md — SOP: the WeChat 4.x control surface

## Why this design

WeChat PC 4.x removed every classic automation surface:

| Surface | Status in 4.x |
|---|---|
| Web protocol (`wx.qq.com`) | dead since ~2017 |
| DLL-hook frameworks (wcferry & co.) | pin WeChat 3.9.x; incompatible with 4.x |
| UIA tree (`wxauto` classic) | 4.x draws with Qt **without accessibility nodes** |

The only community surface verified against 4.2.0.13 (this machine's build) is
[wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica):
**read = local SQLCipher 4 decryption**, **send = UIA + OCR hybrid** after
hot-activating the Qt accessibility gate (one byte inside the running process;
reverts on restart; equivalent to what a screen reader needs).

Alternatives evaluated and rejected: `scottfly189/WeChatAuto.SDK` (.NET host;
core HTTP API gated behind VIP tier), `cluic/wxauto` (3.9.x only), pure-vision
OCR bots (desktop app products, not programmable surfaces).

## Channels

```
                 ┌──────────────────────────────────────────────┐
   agent ──CLI──▶│ utils/wx_backend.py  (the ONLY foreign import)│
                 │   ├─ WeChatDB  ← decrypted db_storage/        │
                 │   └─ WeChat    ← UIA driver + window_session  │
                 └──────────────────────────────────────────────┘
```

- Reading never touches the UI: sessions, contacts, groups, full history,
  incremental reads by `sort_seq`, full-fidelity export (JSON/SQLite).
- Writing always goes through `window_session` (restore-from-tray → topmost →
  act → revert). Without topmost, overlapping windows swallow the clicks —
  reproduced live with an always-on-top assistant UI, fixed by the session.

## Environment variables

| Var | Meaning | Default |
|---|---|---|
| `WX_DB_DIR` | data dir override (contains `<account>/db_storage`) | auto-detect |
| `WX_ACCOUNT` | account subdir pick (e.g. `wxid_xxx_c266`) | auto-pick |
| `WX_WINDOW_MANAGEMENT` | `0` disables window restore/topmost | enabled |

## Closed loop (read → summarize → reply)

```bash
cli-anything-wx --json status                 # 1. health of both channels
cli-anything-wx --json session list           # 2. discover peers
cli-anything-wx msg history 文件传输助手        # 3. read (oldest-first)
cli-anything-wx --json roundtrip 文件传输助手 "hi"   # 4. send + DB proof
```

Poll watermark: remember each chat's last `sort_seq`; `msg new <who> --since SEQ`
returns only newer rows.

## Verified live (WeChat 4.2.0.13, 2026-11-22)

- 19/19 databases decryptable; session list + history reads on a real account
- UIA hot-activation log line: `热激活 UIA：PID=… Weixin.dll+0x…: 0 -> 1`
- Send to 文件传输助手 succeeded ONLY after topmost management; without it
  every click landed on the overlapping assistant window
- Roundtrip proven by decrypted-DB read-back of the exact sent content
- **The focus borrow is architectural**: with WeChat in the background
  (another window foreground), the gated UIA tree collapses to
  `Weixin → MMUIRenderSubWindowHW` — zero controls. Qt only exposes the
  accessibility hierarchy for the ACTIVE window, so "send without ever
  touching the window" is impossible on this surface. The harness therefore
  borrows focus for the send and hands it straight back.

## Could we bypass coordinate clicking entirely? (2026-11 survey)

| Route | Verdict |
|---|---|
| `mrsanshui/WeChatPYAPI` ★1085 | hook/injection, real background sends — but **paid**, closed DLL, pinned to WeChat 4.1.12.26 (requires a client downgrade), antivirus conflicts |
| Commercial iPad-protocol bots (wechaty + padlocal & co.) | protocol-level background sending — paid tokens, grey-market, highest ban risk |
| `SenuMirai/dsh-whale-wechat` "后台发送" | **disproved**: its `wechat-ui-driver.ps1` does `SetForegroundWindow` → `mouse_event`/`SendKeys` → restore previous focus. Fake background (focus flicker hidden) |
| `PostMessage` window-message input | not viable: 4.x is a single Qt render surface (no per-widget HWNDs); the accessibility bridge deactivates with focus |
| Free open-source hooks for 4.2.x | none found (only a macOS anti-recall plugin) |

Conclusion: on free/open surfaces, read = fully background (DB), send =
brief foreground borrow with automatic focus restore (`--hide-after` minimizes
back to tray afterwards). True background sending requires either paying for
a hook SDK pinned to an older build, or a grey protocol service.
