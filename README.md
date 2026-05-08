# SimpleVerify

AstrBot 新成员入群验证插件：新成员需要在限定时间内点击指定表情完成验证，超时则自动移出群聊。

## 工作流程

```
新成员入群 → 机器人发送 @新成员 + 验证表情 + 提示文字
            ├─ 用户点击表情 → 在原消息上附加成功表情 ✓
            └─ 超时未点击   → 移出群聊 ✗
```

- 验证成功后**不发送额外消息**，仅在原验证消息上贴一个成功表情作为标识
- 验证失败则自动将用户移出群聊

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

## 兼容性

- 协议端：OneBot v11（NapCat / Lagrange）
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
