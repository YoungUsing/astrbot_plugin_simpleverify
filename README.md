# SimpleVerify

AstrBot 新成员入群验证插件：新成员需在限定时间内发送指定表情完成验证，超时则自动移出群聊。

## 工作流程

```
新成员入群 → 机器人发送 @新成员 + 验证表情 + 提示文字
            ├─ 用户发送该表情 → 在原消息上贴成功表情 ✓
            └─ 超时未发送       → 移出群聊 ✗
```

- QQ 中点击消息中的表情会将其填入输入框，用户需**手动发送**该表情即可完成验证
- 验证成功后**不发送额外消息**，仅在原验证消息上贴一个成功表情（需要协议端支持 `set_msg_emoji_like`）
- 若协议端不支持贴表情，验证仍然通过（用户不会被踢），仅跳过贴成功表情步骤

## 配置项

在 AstrBot WebUI 的「插件管理」中可直接配置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `verify_face_id` | int | 76 | 验证表情 ID（QQ 表情），如 76=赞、277=滑稽 |
| `success_face_id` | int | 78 | 成功表情 ID（QQ 表情），验证成功时贴在原消息上 |
| `verify_text` | text | 见默认值 | 验证提示文字，`{timeout}` 会被替换为实际秒数 |
| `timeout` | int | 60 | 超时秒数 |
| `enable_groups` | list | `[]` | 启用的群号列表，空 = 全部群生效 |

## 指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/verify @某人` | 管理员 | 手动对指定群成员发起验证 |

## 日志

插件在 AstrBot 控制台输出 `[SimpleVerify]` 前缀的日志，涵盖：
- 初始化状态与当前配置
- 新成员入群检测
- 验证消息发送结果
- 表情贴纸通知接收
- 用户消息中的表情匹配
- 验证成功/超时/踢出结果
- 贴成功表情的每次尝试与结果

## 兼容性

- 协议端：OneBot v11（NapCat / Lagrange），需支持 `on_notice` 注册
- AstrBot 版本：>= v4.0.0

## 安装

将插件仓库克隆到 AstrBot 的 `data/plugins/` 目录下：

```bash
cd AstrBot/data/plugins
git clone https://github.com/YoungUsing/astrbot_plugin_simpleverify
```

然后在 WebUI 的「插件管理」中加载并配置即可。

## 许可

MIT
