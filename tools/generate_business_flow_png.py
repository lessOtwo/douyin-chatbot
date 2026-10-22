"""Render the PRD's business flow as a standalone PNG."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/business-flow.png"
FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
W, H = 2200, 1390
image = Image.new("RGB", (W, H), "#f5f7fb")
draw = ImageDraw.Draw(image)


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def text(x, y, value, size=30, color="#26344d", bold=False, anchor=None):
    draw.text((x, y), value, fill=color, font=font(size, bold), anchor=anchor)


def card(box, title, body, fill="#ffffff", border="#d9e2f2", title_color="#182c53"):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 5, y1 + 8, x2 + 5, y2 + 8), 22, fill="#e5ebf3")
    draw.rounded_rectangle(box, 22, fill=fill, outline=border, width=2)
    text(x1 + 26, y1 + 22, title, 33, title_color, True)
    for i, line in enumerate(body):
        text(x1 + 26, y1 + 72 + i * 34, line, 24, "#53627c")


def arrow(points, color="#7890b6", width=6, label=None, label_xy=None):
    draw.line(points, fill=color, width=width, joint="curve")
    (x1, y1), (x2, y2) = points[-2:]
    if x2 > x1:
        head = [(x2, y2), (x2 - 20, y2 - 11), (x2 - 20, y2 + 11)]
    elif x2 < x1:
        head = [(x2, y2), (x2 + 20, y2 - 11), (x2 + 20, y2 + 11)]
    else:
        head = [(x2, y2), (x2 - 11, y2 - 20), (x2 + 11, y2 - 20)]
    draw.polygon(head, fill=color)
    if label:
        lx, ly = label_xy
        text(lx, ly, label, 23, color, True, "mm")


draw.rounded_rectangle((42, 35, W - 42, 153), 28, fill="#142848")
text(94, 60, "抖音私信 AI 客服｜商家入驻与留资流程", 46, "#ffffff", True)
text(94, 115, "产品方案 · 业务后台 + FastGPT · 2026-10-22", 24, "#b9c9e4")

text(87, 193, "01  准备商家资料", 32, "#254571", True)
card((80, 245, 680, 390), "行业与项目配置", ["行业下维护多个项目", "每个项目单独配置开场问句"])
card((800, 245, 1400, 390), "一次性邀请入驻", ["页面选择行业和项目分组", "填写数字抖音号、昵称及门店资料"])
card((1520, 245, 2120, 390), "平台审核与发布", ["退回重邀修改，管理员转发链接", "通过后生成账号并关联工作流"])
arrow([(680, 319), (800, 319)])
arrow([(1400, 319), (1520, 319)])

text(87, 465, "02  私信对话", 32, "#254571", True)
card((80, 525, 535, 690), "客户私信", ["模糊需求先选项目", "已说明需求则跳过泛问句"])
card((655, 525, 1060, 690), "业务服务", ["去重、会话与状态", "注入已审核项目和资料"])
card((1180, 525, 1585, 690), "FastGPT 工作流", ["同行识别、项目与问句", "同行固定回复 / 留资 / 待处理"])
card((1705, 525, 2120, 690), "业务服务发送", ["校验回复与发送状态", "回写本地会话记录"])
arrow([(535, 608), (655, 608)])
arrow([(1060, 608), (1180, 608)])
arrow([(1585, 608), (1705, 608)])
card((1180, 730, 1585, 808), "答不上来 → 待处理问题", [], "#f1f5fc", "#c8d6ed", "#436494")
arrow([(1382, 690), (1382, 730)], "#9bb0ce", 4)

text(87, 850, "03  留资转化", 32, "#254571", True)
card((80, 915, 480, 1058), "首次主动留资", ["客户有咨询意向", "询问电话或微信"], "#fff8ee", "#f4d5a1", "#96571f")
card((610, 915, 1010, 1058), "客户是否提供？", ["提供：记录本人联系方式", "未提供：进入一次兜底"], "#ffffff", "#c8d7ea")
card((1140, 915, 1550, 1058), "二次兜底索取", ["首次拒绝 / 沉默后", "最多一次；停止则不发"], "#fff8ee", "#f4d5a1", "#96571f")
card((1680, 915, 2120, 1058), "线索候选", ["电话／微信；号码待核验", "导出表格与七天会话链接"], "#eaf7f0", "#afdbc3", "#27724a")
arrow([(480, 986), (610, 986)], "#d3964d")
arrow([(1010, 986), (1140, 986)], "#d3964d", label="未提供", label_xy=(1076, 951))
arrow([(1550, 986), (1680, 986)], "#54a276", label="提供", label_xy=(1615, 951))
arrow([(810, 915), (810, 881), (1900, 881), (1900, 915)], "#54a276", 5, "首次提供", (1315, 867))
card((785, 1155, 1550, 1260), "仍未提供 → 继续站内答疑；停止要求立即生效", ["沉默后主动发送须甲方渠道接口支持"], "#f1f5fc", "#c8d6ed", "#436494")
card((1680, 1155, 2120, 1260), "商家线下联系", ["查看线索并回填跟进结果"], "#eaf7f0", "#afdbc3", "#27724a")
arrow([(1345, 1058), (1345, 1155)], "#9bb0ce", 5, "再次未提供", (1440, 1110))
arrow([(1900, 1058), (1900, 1155)], "#54a276")

draw.line((80, 1310, 2120, 1310), fill="#d8e0ee", width=2)
text(87, 1333, "说明：客服不提供具体报价或方案；商家审核时绑定工作流，真实私信发送仍待接通。", 24, "#6b7890")
image.save(OUTPUT, optimize=True)
print(OUTPUT)
