"""
微信桌面版 UI 自动化框架

架构:
    业务层 (main.py / reader.py)
      ↓
    Page Object 层 (pages/)
      ↓
    核心引擎层 (core/)
      - 多策略元素定位 (快捷键 → UIA名称 → UIA类型 → AI视觉)
      - 自愈机制 (定位失败自动降级并缓存)
      - 安全限流 (随机延迟 + 频率控制)
"""

__version__ = "0.1.0"
