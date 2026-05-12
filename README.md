# SimpleVerify

AstrBot 新成员入群验证插件。新成员入群后需在限定时间内点击指定表情完成验证，超时自动移出群聊。

## 验证模式

| 模式 | 机制 | 难度 |
|------|------|------|
| **低** | 单个表情 — 用户点击消息上的表情即可 | 低 |
| **中** | 多个随机表情 — 用户需根据 CQ 表情码找到目标 | 中 |
| **高** | 表情池 + 描述出题 — 如「点击表示『刀』的表情」 | 较高 |
| **极高** | 表情池 + 谜语出题 — 如「点击下方可用于切的表情」 | 高 |

### 模式细节

- **低模式**：机器人贴单个表情，用户点击该表情完成验证。兼容通过发送表情消息兜底。
- **中模式**：纯随机生成 N 个 QQ 表情 ID（不依赖表情池），用户需根据提示中的 `[CQ:face]` 码找到对应表情。
- **高模式**：从 `recaptcha_emoji_pool` 表情池中随机选取，提示文字为表情的 `desc` 描述。
- **极高模式**：同样使用表情池，但提示文字为表情的 `riddle` 谜语（若无 riddle 则回退到 desc）。

## 工作流程

```
新成员入群 → 机器人 @新成员 + 提示文字 + 贴 N 个表情
            ├─ 用户点击正确表情 → 移除干扰表情 → 贴成功表情 ✓
            └─ 超时未完成         → 移出群聊 + 回复失败原因 ✗
```

- 验证通过后自动移除所有干扰表情，仅保留成功表情
- 验证通过**不发送额外消息**，仅在原消息上贴成功表情
- 低模式额外提供消息兜底：用户发送对应表情也能通过

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable` | bool | false | 插件总开关 |
| `verify_method` | string | 低 | 验证模式：低 / 中 / 高 / 极高 |
| `verify_face_id` | int | 414 | 低模式验证表情 ID |
| `success_face_id` | int | 144 | 验证成功后贴的表情 ID |
| `verify_text` | text | — | 低模式提示文字，`{timeout}` = 超时秒数 |
| `verify_text_medium` | text | — | 中模式提示文字，`{timeout}` + `{extra}`（CQ 表情码） |
| `verify_text_high` | text | — | 高/极高模式提示文字，`{timeout}` + `{extra}`（描述指引） |
| `timeout` | int | 120 | 验证超时秒数 |
| `challenge_count` | int | 4 | 中/高/极高模式贴出的表情总数 |
| `enable_groups` | list | `[]` | 启用的群号，空 = 全部群生效 |
| `recaptcha_emoji_pool` | template_list | 9 个表情 | 高/极高模式的表情池，每项含 `id`、`desc`、`riddle` |

## 指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/verify @某人` | 管理员 | 手动对指定成员发起验证 |

## 兼容性

- 协议端：OneBot v11（NapCat / Lagrange），需支持 `on_notice` 注册和 `set_msg_emoji_like` API
- AstrBot 版本：>= v4.0.0

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/YoungUsing/astrbot_plugin_simpleverify
```

在 WebUI「插件管理」中加载并配置即可。

## 许可

MIT
