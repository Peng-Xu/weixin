"""
微信消息读取 - 主入口

使用方法:
  1. 确保微信 PC 客户端已打开并登录
  2. pip install -r requirements.txt
  3. 复制 config.example.yaml 为 config.yaml 并按需修改
  4. 运行:
     python -m ui.main                          # 读取所有会话
     python -m ui.main --contact "张三"          # 读取指定联系人
     python -m ui.main --monitor                 # 实时监控模式
     python -m ui.main --export                  # 导出已存储消息
     python -m ui.main --stats                   # 查看统计信息
"""

from __future__ import annotations

import os
import sys
import signal
import argparse
import time

import yaml
from loguru import logger

from .reader import MessageReader


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置"""
    default_config = {
        "safety": {"min_delay": 0.5, "max_delay": 2.0},
        "reader": {
            "max_scrolls_per_chat": 50,
            "save_to_db": True,
            "db_path": "wechat_messages.db",
        },
        "monitor": {"interval": 5.0, "contacts": []},
        "batch": {
            "mode": "all",
            "contacts": [],
            "skip_sessions": ["微信支付", "微信团队", "微信运动", "服务通知"],
        },
        "export": {"format": "json", "output_dir": "exports"},
        "debug": {
            "screenshot": False,
            "screenshot_dir": "logs/screenshots",
            "strategy_cache": "logs/strategy_cache.json",
            "log_level": "INFO",
        },
    }

    # 在 ui 目录下查找配置
    ui_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(ui_dir, config_path)
    if not os.path.exists(config_file):
        config_file = config_path

    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        # 简单合并
        for key, val in user_config.items():
            if key in default_config and isinstance(val, dict):
                default_config[key].update(val)
            else:
                default_config[key] = val
        logger.info(f"配置已加载: {config_file}")
    else:
        logger.warning(f"未找到配置文件 {config_file}, 使用默认配置")

    return default_config


def setup_logging(config: dict):
    """配置日志"""
    debug_conf = config.get("debug", {})
    level = debug_conf.get("log_level", "INFO")

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:8s}</level> | {message}",
    )
    logger.add(
        "logs/ui_reader.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


def create_reader(config: dict) -> MessageReader:
    """根据配置创建 MessageReader"""
    safety = config["safety"]
    reader_conf = config["reader"]
    debug_conf = config["debug"]

    return MessageReader(
        db_path=reader_conf["db_path"],
        min_delay=safety["min_delay"],
        max_delay=safety["max_delay"],
        screenshot_dir=debug_conf["screenshot_dir"] if debug_conf["screenshot"] else None,
        strategy_cache=debug_conf["strategy_cache"],
    )


def cmd_read_all(reader: MessageReader, config: dict):
    """读取所有/指定会话的消息"""
    batch_conf = config["batch"]
    reader_conf = config["reader"]

    if batch_conf["mode"] == "list" and batch_conf["contacts"]:
        results = reader.read_specific_contacts(
            batch_conf["contacts"],
            max_scrolls=reader_conf["max_scrolls_per_chat"],
        )
    else:
        results = reader.read_all_sessions(
            max_scrolls_per_chat=reader_conf["max_scrolls_per_chat"],
            skip_sessions=batch_conf.get("skip_sessions", []),
        )

    # 打印摘要
    print("\n" + "=" * 60)
    print("读取完成摘要:")
    print("=" * 60)
    for name, msgs in results.items():
        print(f"  {name}: {len(msgs)} 条消息")
    total = sum(len(m) for m in results.values())
    print(f"\n  总计: {len(results)} 个会话, {total} 条消息")


def cmd_read_contact(reader: MessageReader, contact_name: str, config: dict):
    """读取指定联系人的消息"""
    messages = reader.read_chat_messages(
        contact_name,
        max_scrolls=config["reader"]["max_scrolls_per_chat"],
    )

    print(f"\n[{contact_name}] 共 {len(messages)} 条消息:")
    print("-" * 40)
    for msg in messages[-20:]:  # 显示最后20条
        print(f"  {msg}")
    if len(messages) > 20:
        print(f"  ... (仅显示最后 20 条, 共 {len(messages)} 条)")


def cmd_monitor(reader: MessageReader, config: dict):
    """实时监控模式"""
    monitor_conf = config["monitor"]
    contacts = monitor_conf.get("contacts") or None

    def on_new_message(contact: str, msg):
        print(f"[新消息][{contact}] {msg}")

    reader.start_monitoring(
        contact_names=contacts,
        interval=monitor_conf["interval"],
        on_new_message=on_new_message,
    )

    print("实时监控已启动, 按 Ctrl+C 停止...")

    # 等待退出信号
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while running:
        time.sleep(1)

    reader.stop_monitoring()


def cmd_export(reader: MessageReader, config: dict, contact: str | None = None):
    """导出消息"""
    export_conf = config["export"]
    fmt = export_conf.get("format", "json")
    out_dir = export_conf.get("output_dir", "exports")

    if contact:
        filename = f"{out_dir}/{contact}_messages"
    else:
        filename = f"{out_dir}/all_messages"

    path = reader.export_messages(filename, contact_name=contact, format=fmt)
    print(f"消息已导出到: {path}")


def cmd_stats(reader: MessageReader):
    """显示统计信息"""
    stats = reader.get_stats()

    print("\n" + "=" * 50)
    print("消息统计:")
    print("=" * 50)
    print(f"  总消息数: {stats['total_messages']}")
    print(f"  联系人数: {stats['total_contacts']}")
    print(f"  数据库:   {stats['db_path']}")

    if stats['top_contacts']:
        print("\n  消息最多的联系人:")
        for item in stats['top_contacts']:
            print(f"    {item['name']}: {item['count']} 条")


def main():
    parser = argparse.ArgumentParser(
        description="微信消息读取工具 (UI自动化)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m ui.main                        # 读取所有会话消息
  python -m ui.main -c "张三"              # 读取指定联系人
  python -m ui.main -c "张三" -c "工作群"  # 读取多个联系人
  python -m ui.main --monitor              # 实时监控新消息
  python -m ui.main --export               # 导出所有消息
  python -m ui.main --export -c "张三"     # 导出指定联系人消息
  python -m ui.main --stats                # 查看统计信息
        """,
    )
    parser.add_argument(
        "-c", "--contact",
        action="append",
        help="指定联系人/群名 (可多次使用)",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="实时监控模式",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="导出已存储的消息",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="显示消息统计信息",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--scrolls",
        type=int,
        default=None,
        help="覆盖每个聊天的最大滚动次数",
    )

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)
    setup_logging(config)

    if args.scrolls:
        config["reader"]["max_scrolls_per_chat"] = args.scrolls

    # 创建 Reader
    reader = create_reader(config)

    # 纯数据操作不需要连接微信窗口
    if args.stats:
        cmd_stats(reader)
        reader.close()
        return

    if args.export and not args.monitor:
        contact = args.contact[0] if args.contact else None
        cmd_export(reader, config, contact)
        reader.close()
        return

    # 需要连接微信窗口的操作
    if not reader.connect():
        print("错误: 无法连接微信窗口")
        print("请确保:")
        print("  1. 微信 PC 客户端已打开并登录")
        print("  2. 微信窗口未被最小化到托盘")
        reader.close()
        sys.exit(1)

    try:
        if args.monitor:
            cmd_monitor(reader, config)
        elif args.contact:
            if len(args.contact) == 1:
                cmd_read_contact(reader, args.contact[0], config)
            else:
                results = reader.read_specific_contacts(
                    args.contact,
                    max_scrolls=config["reader"]["max_scrolls_per_chat"],
                )
                for name, msgs in results.items():
                    print(f"\n[{name}] {len(msgs)} 条消息")
        else:
            cmd_read_all(reader, config)
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        reader.close()


if __name__ == "__main__":
    main()
