#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用 TypeSafe Jev (System One)：A股短线策略适配性 + 候选标的买点结构化判断"""
import json, os, sys, urllib.request, urllib.error

API_URL = "https://api.typesafe.ai/v1/systemone"

import datadir

def load_env(path=None):
    if path is None:
        path = os.path.join(datadir.data_dir(), ".env")
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

state = {
    "评估日期": "2026-09-20（周日）；上一交易日为 2026-09-18（周五）",
    "投资者画像_假设": {
        "身份": "A股个人散户，短线情绪交易学习者",
        "资金": "小资金（约5万-30万人民币）",
        "盯盘条件": "非全职交易，白天上班，手机或普通电脑+普通券商通道，无Level-2/极速柜台/量化工具",
        "经验": "了解打板/低吸/情绪周期概念，独立执行经验有限",
        "诉求": "想用游资（陈小群/小鳄鱼）战法在A股做短线"
    },
    "当前市场状态_2026_09_18": {
        "量能": "沪深两市成交额突破2万亿元，显著放量",
        "指数": "沪指放量涨近1%收复3900点；科创50单日涨近3%；创业板指涨超2%",
        "主线": "AI算力硬件+芯片半导体双轮共振掀涨停潮",
        "暗线": "地产链/地产租赁午后异军突起，超6亿资金买入地产龙头股",
        "次新": "近端次新股集体爆发，有人气股2日最高涨超17倍",
        "连板梯队": {
            "6连板": "闽东电力(000993，电力+地产暗线龙头)",
            "4连板": "澳弘电子(605058，PCB)",
            "3连板": "博汇科技(688004，科创板20cm)",
            "2连板": "华瓷股份(001216)、通鼎互联(002491)、西陇科学"
        },
        "主线中军": "华天科技(002185)：先进封装+存储封测+29.96亿收购华羿微电；涨停封单7.17亿、换手10.92%、主力净流入超27亿居两市第一，属低位启动位"
    },
    "战法规则": {
        "陈小群战法": {
            "核心": "只做主线情绪龙头；涨停只分4类，只做龙头确认板；买在分歧、卖在一致",
            "买点": "1)低位首板：新主线题材+大资金净流入+位置低；2)龙头主升中首次大分歧（放量不破5日线/关键位）低吸；3)不做杂毛跟风板",
            "卖点": "一致加速（缩量涨停/一字板）兑现；断板/炸板走弱立即离场",
            "仓位": "集中做龙头，情绪冰点空仓等周期回暖"
        },
        "小鳄鱼战法": {
            "核心": "仓位管控优先级高于交易技术，单次大回撤可能亏空本金",
            "买点": "1)首板直接买中大牛难度大，要确定性：二板/弱转强（前日分歧、次日超预期高开走强）是关键买点；2)弱势行情只做超跌+缩量+资金回流；3)强者恒强只做最强",
            "仓位": "弱势轻仓或空仓；打板只用小仓位试错，确认后再加；单票有硬上限"
        }
    },
    "生态警示": "2026年4月中国基金报报道：陈小群退网、流沙河投降、96余哥注销公众号；量化高频挤压+监管趋严，传统打板接力生态重构，散户打板胜率与性价比显著下降"
}

questions = {
    "策略适配": {
        "type": "choice",
        "instructions": "综合`投资者画像_假设`、`当前市场状态_2026_09_18`、`战法规则`与`生态警示`，从风险调整后收益角度，该投资者当下最适合以哪种买入模式为主策略？",
        "criteria": {
            "主线龙头分歧低吸": "不追涨停，只在主线龙头（中军或连板龙）分歧回踩（5日线附近、放量不破位）时低吸，中等仓位",
            "低位首板半路低吸": "只做新主线低位首板（题材首次发酵+大资金净流入+低位），半路或次日低吸，不排板不追高位",
            "二板弱转强确认": "小鳄鱼式：首板只观察，次日弱转强（超预期高开放量走强）确认后买入，牺牲空间换确定性",
            "高位连板打板接力": "陈小群式打板/排板接力3板以上龙头（如闽东电力、澳弘电子），追求情绪溢价",
            "放弃个股短线改ETF波段": "个股短线对该画像风险过大，改做芯片/算力行业ETF波段或定投"
        }
    },
    "打板接力是否适合": {
        "type": "noul",
        "instructions": "结合`投资者画像_假设`（非全职、无极速通道、经验有限）与`生态警示`（量化挤压、打板生态重构），该投资者当前把'涨停打板接力'作为主策略是否合适？",
        "criteria": {"true": "适合作为主策略", "false": "不适合，最多极小仓位试错或不做"}
    },
    "情绪周期阶段": {
        "type": "score",
        "instructions": "根据`当前市场状态_2026_09_18`（2万亿放量、次新2日17倍、6板空间龙、主线涨停潮、收复3900点），当前A股短线情绪周期最接近哪个阶段？",
        "criteria": [
            "冰点：亏钱效应弥漫，连板绝迹，量能萎缩",
            "回暖：首板增多，高度板止跌回升",
            "发酵：主线明确，涨停潮出现，空间逐步打开",
            "高潮过热：次新暴涨数倍、放量到极致、全民看多",
            "分歧退潮：高位板批量炸板断板，亏钱效应扩散"
        ]
    },
    "华天科技002185低吸": {
        "type": "noul",
        "instructions": {
            "标的": "华天科技(002185)：主线中军，先进封装+存储封测+收购华羿微电；涨停封单7.17亿、换手10.92%、主力净流入27亿居两市第一；处于低位启动位",
            "question": "按`战法规则.陈小群战法`'主线+低位+大资金'标准，该票下周是否属于值得小仓位介入的候选（理想方式是分歧回踩低吸而非追高）？"
        },
        "criteria": {"true": "符合：主线地位+低位+资金强，回踩即买点", "false": "不符合或风险收益比差"}
    },
    "闽东电力000993接力": {
        "type": "noul",
        "instructions": {
            "标的": "闽东电力(000993)：6连板空间龙头，但属于地产/电力暗线而非第一主线",
            "question": "按`战法规则`与`投资者画像_假设`，该票下周一是否适合追高/打板接力？"
        },
        "criteria": {"true": "适合小仓位接力", "false": "不适合：高度太高/非主线/断板风险大"}
    },
    "澳弘电子605058接力": {
        "type": "noul",
        "instructions": {
            "标的": "澳弘电子(605058)：4连板，PCB（属于AI算力主线）",
            "question": "按`战法规则`与`投资者画像_假设`，该票下周一是否适合追高/打板接力？"
        },
        "criteria": {"true": "适合小仓位接力", "false": "不适合：位置偏高/风险收益比差"}
    },
    "博汇科技688004接力": {
        "type": "noul",
        "instructions": {
            "标的": "博汇科技(688004)：3连板，科创板20cm涨跌幅，波动放大",
            "question": "按`战法规则`与`投资者画像_假设`，该票下周一是否适合追高/打板接力？"
        },
        "criteria": {"true": "适合小仓位接力", "false": "不适合：20cm波动+位置风险"}
    },
    "华瓷股份001216二板确认": {
        "type": "noul",
        "instructions": {
            "标的": "华瓷股份(001216)：2连板",
            "question": "按`战法规则.小鳄鱼战法`'二板/弱转强确认'标准，若周一该票超预期高开且放量走强（弱转强），是否属于可小仓位操作的确认买点标的？"
        },
        "criteria": {"true": "是：弱转强确认后可小仓试错", "false": "否：题材/地位不支持"}
    }
}

def main():
    env = load_env()
    key = env.get("TYPESAFE_API_KEY", "")
    model = env.get("TYPESAFE_MODEL", "jev-latest")
    if not key:
        sys.exit("ERROR: TYPESAFE_API_KEY 为空")
    payload = {"state": state, "model": model, "questions": questions}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:800]))
    with open(os.path.join(datadir.data_dir(), "jev_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
