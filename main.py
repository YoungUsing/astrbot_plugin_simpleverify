import asyncio

import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig


@register("astrbot_plugin_simpleverify", "YoungUsing", "新成员入群验证：贴表情完成验证，超时自动移出", "1.0.3")
class SimpleVerify(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._pending_verify: dict[tuple[str, str], asyncio.Task] = {}
        self._verify_msg_ids: dict[tuple[str, str], str] = {}
        self._group_umo: dict[str, str] = {}
        self._client = None
        self._self_id: str = ""
        self._seen_reactions: set[tuple[str, str, str]] = set()

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
                    logger.warning("[SimpleVerify] 当前协议端不支持 on_notice 注册")
                # 提前获取 bot 自己的 QQ 号
                login_info = await self._client.api.call_action("get_login_info")
                self._self_id = str(login_info.get("user_id", "")) if isinstance(login_info, dict) else ""
                logger.info(f"[SimpleVerify] bot self_id = {self._self_id}")
        except Exception as e:
            logger.error(f"[SimpleVerify] 初始化失败: type={type(e).__name__}, {e}")
        logger.info(
            f"[SimpleVerify] 配置: verify_face_id={self.config.get('verify_face_id', 76)}, "
            f"success_face_id={self.config.get('success_face_id', 78)}, "
            f"timeout={self.config.get('timeout', 60)}s, "
            f"enable_groups={self.config.get('enable_groups', [])}"
        )

    # ── OneBot 通知事件 ────────────────────────────────────────

    async def _on_notice(self, event: dict):
        notice_type = event.get("notice_type", "")

        if not self.config.get("enable", True):
            return

        if notice_type == "group_increase":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))
            self_id = str(event.get("self_id", ""))
            logger.info(f"[SimpleVerify] 新成员入群: group_id={group_id}, user_id={user_id}")

            if group_id not in self._group_umo:
                self._group_umo[group_id] = f"aiocqhttp:{self_id}:{group_id}"

            enabled = self.config.get("enable_groups", [])
            if enabled and group_id not in [str(g) for g in enabled]:
                logger.info(f"[SimpleVerify] 群 {group_id} 不在启用列表中，跳过")
                return

            await self._start_verify(group_id, user_id)

        elif notice_type == "group_msg_emoji_like":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))
            message_id = str(event.get("message_id", ""))
            is_add = event.get("is_add", True)

            # 忽略机器人自己贴的表情
            if self._self_id and user_id == self._self_id:
                return
            if not is_add:
                return

            dedup_key = (group_id, user_id, message_id)
            if dedup_key in self._seen_reactions:
                return
            if len(self._seen_reactions) > 500:
                self._seen_reactions.clear()
            self._seen_reactions.add(dedup_key)

            logger.info(
                f"[SimpleVerify] 贴表情通知: group_id={group_id}, "
                f"user_id={user_id}, message_id={message_id}"
            )
            await self._handle_reaction(group_id, user_id, message_id)

    # ── 验证流程 ──────────────────────────────────────────────

    async def _send_verify_msg(self, group_id: str, user_id: str) -> str | None:
        """发送验证消息文本。返回 message_id。"""
        timeout = int(self.config.get("timeout", 60))
        text = self.config.get(
            "verify_text",
            "新人验证：请在 {timeout} 秒内贴表情完成验证，超时将被移出群聊。",
        )
        text = text.replace("{timeout}", str(timeout))

        cq_message = f"[CQ:at,qq={user_id}]\n{text}"

        try:
            result = await self._client.api.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=cq_message,
            )
        except Exception as e:
            logger.error(f"[SimpleVerify] 发送验证消息失败: type={type(e).__name__}, {e}")
            return None

        message_id = str(result.get("message_id", "")) if isinstance(result, dict) else None
        if not message_id:
            logger.error(f"[SimpleVerify] 未能提取 message_id: {result}")
        return message_id

    async def _start_verify(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        timeout = int(self.config.get("timeout", 60))
        verify_face = str(self.config.get("verify_face_id", 76))

        logger.info(f"[SimpleVerify] 开始验证: group_id={group_id}, user_id={user_id}, timeout={timeout}s")

        message_id = await self._send_verify_msg(group_id, user_id)
        if not message_id:
            logger.error("[SimpleVerify] 验证消息发送失败，放弃本次验证")
            return

        # 先存 message_id，再贴表情，防止贴表情的通知提前到达时找不到
        self._verify_msg_ids[key] = message_id

        if key in self._pending_verify:
            self._pending_verify[key].cancel()

        task = asyncio.create_task(self._verify_timeout(group_id, user_id, timeout))
        self._pending_verify[key] = task
        logger.info(f"[SimpleVerify] 超时任务已创建，剩余 {timeout}s")

        # 贴验证表情（在 _verify_msg_ids 注册之后，防止异步通知提前到达）
        try:
            await self._client.api.call_action(
                "set_msg_emoji_like",
                message_id=message_id,
                emoji_id=verify_face,
            )
            logger.info(f"[SimpleVerify] 验证表情已贴: face={verify_face}, msg_id={message_id}")
        except Exception as e:
            logger.warning(f"[SimpleVerify] 贴验证表情失败: type={type(e).__name__}, {e}")

    async def _verify_timeout(self, group_id: str, user_id: str, timeout: int):
        key = (group_id, user_id)
        try:
            await asyncio.sleep(timeout)
            logger.info(f"[SimpleVerify] 验证超时: group_id={group_id}, user_id={user_id}")

            message_id = self._verify_msg_ids.get(key, "")
            _, detail = await self._kick_user(group_id, user_id)

            if message_id:
                reply_text = f"[CQ:reply,id={message_id}]验证失败：{detail}"
                try:
                    await self._client.api.call_action(
                        "send_group_msg",
                        group_id=int(group_id),
                        message=reply_text,
                    )
                    logger.info(f"[SimpleVerify] 失败通知已发送: {detail}")
                except Exception as e:
                    logger.warning(f"[SimpleVerify] 发送失败通知失败: type={type(e).__name__}, {e}")
        except asyncio.CancelledError:
            logger.info(f"[SimpleVerify] 验证任务被取消（用户已验证成功）: user_id={user_id}")
        finally:
            self._pending_verify.pop(key, None)
            self._verify_msg_ids.pop(key, None)

    async def _kick_user(self, group_id: str, user_id: str) -> tuple[bool, str]:
        try:
            await self._client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False,
            )
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            logger.error(f"[SimpleVerify] set_group_kick 异常: {detail}")
            if "permission" in str(e).lower() or "auth" in str(e).lower():
                return False, "权限不足，需管理员手动处理"
            return False, detail

        # set_group_kick 成功/失败都返回 None，通过查群成员信息验证
        try:
            await self._client.api.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
            )
            return False, "踢出失败，可能权限不足，需管理员手动处理"
        except Exception:
            return True, "已踢"

    # ── 贴表情验证 ────────────────────────────────────────────

    async def _handle_reaction(self, group_id: str, user_id: str, message_id: str):
        key = (group_id, user_id)
        expected_msg_id = self._verify_msg_ids.get(key)
        if not expected_msg_id or message_id != expected_msg_id:
            return
        logger.info(f"[SimpleVerify] 贴表情匹配成功，user_id={user_id} 验证通过")
        await self._verify_success(group_id, user_id)

    async def _verify_success(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        message_id = self._verify_msg_ids.pop(key, None)
        if not message_id:
            return

        logger.info(f"[SimpleVerify] 验证成功: user_id={user_id}")

        success_face = str(self.config.get("success_face_id", 78))
        try:
            await self._client.api.call_action(
                "set_msg_emoji_like",
                message_id=message_id,
                emoji_id=success_face,
            )
            logger.info(f"[SimpleVerify] 成功表情已贴: face={success_face}, msg_id={message_id}")
        except Exception as e:
            logger.warning(f"[SimpleVerify] 贴成功表情失败: type={type(e).__name__}, {e}")

        self._cancel_verify(key)

    def _cancel_verify(self, key: tuple[str, str]):
        if key in self._pending_verify:
            self._pending_verify[key].cancel()
            logger.debug(f"[SimpleVerify] 验证任务已取消: key={key}")

    # ── 指令 ──────────────────────────────────────────────────

    @filter.command("verify")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def manual_verify(self, event: AstrMessageEvent):
        """手动对新成员发起验证：/verify @某人"""
        if not self.config.get("enable", True):
            yield event.plain_result("[SimpleVerify] 插件已关闭")
            return
        group_id = event.get_group_id()
        target_user_id = None

        for comp in event.get_messages():
            if isinstance(comp, Comp.At):
                qq = str(comp.qq)
                if qq != self._self_id:
                    target_user_id = qq
                    break

        if not target_user_id:
            logger.info(f"[SimpleVerify] /verify 指令未找到 @目标，发送者={event.get_sender_id()}")
            yield event.plain_result("[SimpleVerify] 请 @ 要验证的群成员，例如：/verify @某人")
            return

        self._group_umo[group_id] = event.unified_msg_origin

        logger.info(
            f"[SimpleVerify] 管理员 {event.get_sender_id()} 手动发起验证: "
            f"target={target_user_id}, group={group_id}"
        )
        await self._start_verify(group_id, target_user_id)
        event.stop_event()

    # ── 群消息兜底 ────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """缓存 UMO，并兜底检测用户发送验证表情的消息"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        key = (group_id, user_id)

        self._group_umo[group_id] = event.unified_msg_origin

        if key not in self._pending_verify:
            return

        verify_face = int(self.config.get("verify_face_id", 76))
        for comp in event.get_messages():
            if isinstance(comp, Comp.Face) and str(comp.id) == str(verify_face):
                logger.info(f"[SimpleVerify] 用户 {user_id} 通过发送表情消息完成验证（兜底）")
                await self._verify_success(group_id, user_id)
                event.stop_event()
                return

    # ── 卸载 ──────────────────────────────────────────────────

    async def terminate(self):
        for task in self._pending_verify.values():
            task.cancel()
        self._pending_verify.clear()
        self._verify_msg_ids.clear()
