import asyncio

import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig


@register("astrbot_plugin_simpleverify", "YoungUsing", "新成员入群验证：点击表情完成验证，超时自动移出", "1.0.1")
class SimpleVerify(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._pending_verify: dict[tuple[str, str], asyncio.Task] = {}
        self._verify_msg_ids: dict[tuple[str, str], str] = {}
        self._client = None

    async def initialize(self):
        logger.info("[SimpleVerify] 插件初始化中...")
        try:
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            if platform:
                self._client = platform.get_client()
                logger.info(f"[SimpleVerify] 获取到 OneBot 客户端: {type(self._client).__name__}")
                if hasattr(self._client, "on_notice"):
                    self._client.on_notice(self._on_notice)
                    logger.info("[SimpleVerify] 已注册 OneBot 通知事件处理器")
                else:
                    logger.warning("[SimpleVerify] 当前协议端不支持 on_notice 注册，将仅依赖消息检测")
            else:
                logger.warning("[SimpleVerify] 未找到 AIOCQHTTP 平台，插件可能无法正常工作")
        except Exception as e:
            logger.error(f"[SimpleVerify] 初始化失败: {e}", exc_info=True)
        logger.info(
            f"[SimpleVerify] 配置: verify_face_id={self.config.get('verify_face_id', 76)}, "
            f"success_face_id={self.config.get('success_face_id', 78)}, "
            f"timeout={self.config.get('timeout', 60)}s, "
            f"enable_groups={self.config.get('enable_groups', [])}"
        )

    async def _on_notice(self, event: dict):
        logger.debug(f"[SimpleVerify] 收到通知事件: {event}")
        notice_type = event.get("notice_type", "")

        if notice_type == "group_increase":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))
            logger.info(f"[SimpleVerify] 检测到新成员入群: group_id={group_id}, user_id={user_id}")

            enabled = self.config.get("enable_groups", [])
            if enabled:
                enabled_str = [str(g) for g in enabled]
                if group_id not in enabled_str:
                    logger.info(f"[SimpleVerify] 群 {group_id} 不在启用列表中，跳过")
                    return

            await self._start_verify(group_id, user_id)

        elif notice_type == "notify":
            sub_type = event.get("sub_type", "")
            logger.debug(f"[SimpleVerify] 通知子类型: {sub_type}")
            if sub_type in ("emoji_like", "msg_emoji_like"):
                group_id = str(event.get("group_id", ""))
                user_id = str(event.get("user_id", ""))
                message_id = str(event.get("message_id", ""))
                logger.info(
                    f"[SimpleVerify] 收到表情贴纸通知: group_id={group_id}, "
                    f"user_id={user_id}, message_id={message_id}"
                )
                await self._on_reaction(group_id, user_id, message_id)

    async def _start_verify(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        verify_face = str(self.config.get("verify_face_id", 76))
        timeout = int(self.config.get("timeout", 60))
        text = self.config.get(
            "verify_text",
            "新人验证：请在 {timeout} 秒内发送上方表情完成验证，超时将被移出群聊。",
        )
        text = text.replace("{timeout}", str(timeout))

        logger.info(f"[SimpleVerify] 开始验证: group_id={group_id}, user_id={user_id}, timeout={timeout}s")

        if not self._client:
            logger.error("[SimpleVerify] _client 为 None，无法发送验证消息")
            return

        # 使用 CQ 码字符串格式发送，兼容性更好
        cq_message = f"[CQ:at,qq={user_id}] [CQ:face,id={verify_face}]\n{text}"

        try:
            result = await self._client.api.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=cq_message,
            )
            logger.info(f"[SimpleVerify] send_group_msg 返回: {result}")
            message_id = str(result.get("message_id", ""))
            self._verify_msg_ids[key] = message_id
            logger.info(
                f"[SimpleVerify] 验证消息已发送: group_id={group_id}, user_id={user_id}, "
                f"message_id={message_id}"
            )
        except Exception as e:
            logger.error(
                f"[SimpleVerify] 发送验证消息失败: type={type(e).__name__}, "
                f"msg={e}, args={e.args}"
            )
            return

        if key in self._pending_verify:
            logger.info(f"[SimpleVerify] 取消 user_id={user_id} 的旧验证任务")
            self._pending_verify[key].cancel()

        task = asyncio.create_task(self._verify_timeout(group_id, user_id, timeout))
        self._pending_verify[key] = task
        logger.info(f"[SimpleVerify] 超时任务已创建，剩余 {timeout}s")

    async def _verify_timeout(self, group_id: str, user_id: str, timeout: int):
        try:
            await asyncio.sleep(timeout)
            logger.info(f"[SimpleVerify] 验证超时: group_id={group_id}, user_id={user_id}")
            await self._kick_user(group_id, user_id)
        except asyncio.CancelledError:
            logger.info(f"[SimpleVerify] 验证任务被取消（用户已验证成功）: user_id={user_id}")
        finally:
            key = (group_id, user_id)
            self._pending_verify.pop(key, None)
            self._verify_msg_ids.pop(key, None)

    async def _kick_user(self, group_id: str, user_id: str):
        try:
            result = await self._client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False,
            )
            logger.info(f"[SimpleVerify] 已将 {user_id} 从群 {group_id} 移出（验证超时），结果: {result}")
        except Exception as e:
            logger.error(f"[SimpleVerify] 移出用户 {user_id} 失败: {e}", exc_info=True)

    async def _on_reaction(self, group_id: str, user_id: str, message_id: str):
        key = (group_id, user_id)
        expected_msg_id = self._verify_msg_ids.get(key)
        logger.debug(
            f"[SimpleVerify] 表情贴纸匹配检查: expected_msg_id={expected_msg_id}, "
            f"received_msg_id={message_id}"
        )
        if expected_msg_id and message_id == expected_msg_id:
            logger.info(f"[SimpleVerify] 表情贴纸匹配成功，user_id={user_id} 即将验证通过")
            await self._verify_success(group_id, user_id)
        else:
            logger.debug(f"[SimpleVerify] 表情贴纸不匹配或用户不在验证列表中")

    async def _verify_success(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        message_id = self._verify_msg_ids.get(key)
        if not message_id:
            logger.warning(f"[SimpleVerify] 未找到 user_id={user_id} 的验证消息ID，无法贴成功表情")
            self._cancel_verify(key)
            return

        success_face = str(self.config.get("success_face_id", 78))
        logger.info(f"[SimpleVerify] 验证成功: user_id={user_id}, 尝试贴成功表情 face_id={success_face}")

        # 尝试多种参数格式，兼容不同版本的 NapCat / Lagrange
        attempts = [
            {"emoji_id": success_face},
            {"emoji_id": int(success_face)},
            {"face_id": success_face},
        ]
        attached = False
        for i, kwargs in enumerate(attempts):
            try:
                logger.debug(f"[SimpleVerify] 尝试贴表情 (方式 {i+1}): {kwargs}")
                await self._client.api.call_action(
                    "set_msg_emoji_like",
                    message_id=message_id,
                    **kwargs,
                )
                logger.info(f"[SimpleVerify] 成功贴表情 (方式 {i+1}): {kwargs}")
                attached = True
                break
            except Exception as e:
                logger.warning(f"[SimpleVerify] 贴表情失败 (方式 {i+1}, {kwargs}): {e}")

        if not attached:
            logger.warning("[SimpleVerify] 所有贴表情方式均失败，当前协议端可能不支持 set_msg_emoji_like")

        self._cancel_verify(key)

    def _cancel_verify(self, key: tuple[str, str]):
        if key in self._pending_verify:
            self._pending_verify[key].cancel()
            logger.debug(f"[SimpleVerify] 验证任务已取消: key={key}")

    @filter.command("verify")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def manual_verify(self, event: AstrMessageEvent):
        """手动对新成员发起验证：/verify @某人"""
        group_id = event.get_group_id()
        target_user_id = None

        for comp in event.get_messages():
            if isinstance(comp, Comp.At):
                target_user_id = str(comp.qq)
                break

        if not target_user_id:
            logger.info(f"[SimpleVerify] /verify 指令未找到 @目标，发送者={event.get_sender_id()}")
            yield event.plain_result("[SimpleVerify] 请 @ 要验证的群成员，例如：/verify @某人")
            return

        if not self._client:
            logger.error("[SimpleVerify] /verify 指令时 _client 为 None")
            yield event.plain_result("[SimpleVerify] 未连接到 OneBot 协议端，无法发送验证")
            return

        logger.info(
            f"[SimpleVerify] 管理员 {event.get_sender_id()} 手动发起验证: "
            f"target={target_user_id}, group={group_id}"
        )
        await self._start_verify(group_id, target_user_id)
        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群聊消息，检测验证用户是否通过发送表情来完成验证"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        key = (group_id, user_id)

        if key not in self._pending_verify:
            return

        verify_face = int(self.config.get("verify_face_id", 76))
        logger.debug(f"[SimpleVerify] 验证中的用户 {user_id} 发了一条消息，检查是否包含验证表情...")
        for comp in event.get_messages():
            if isinstance(comp, Comp.Face):
                logger.debug(f"[SimpleVerify] 检测到 Face 组件: id={comp.id}")
                if str(comp.id) == str(verify_face):
                    logger.info(f"[SimpleVerify] 用户 {user_id} 通过发送表情完成验证")
                    await self._verify_success(group_id, user_id)
                    event.stop_event()
                    return

    async def terminate(self):
        logger.info("[SimpleVerify] 插件卸载中，清理验证任务...")
        for key, task in self._pending_verify.items():
            logger.info(f"[SimpleVerify] 取消验证任务: {key}")
            task.cancel()
        self._pending_verify.clear()
        self._verify_msg_ids.clear()
        logger.info("[SimpleVerify] 插件已卸载")
