1.编号体系没有统一标准，实际文档中大量混用：
第一章 总则            ← 纯汉字
1.1 适用范围           ← 纯数字
（一）效力等级          ← 括号 + 汉字
A.1 术语说明           ← 英文 + 数字
第3章 管理要求          ← 汉字 + 阿拉伯数字
正则穷举这些形态必然脆弱，且一旦出现规则外格式就静默降级（层级信息丢失），用户毫无感知。更隐蔽的问题是正则会误命中："详见第 3 条"这类正文引用、"第一，我们要坚持……"这类序数词开头，都会被错误识别为标题。
根本原因：编号体系是文档级全局属性（1.1 的层级取决于文档里还存在哪些编号），单行正则无法判断。

2.需要llm分析，而且其中包含一些细节：
    文本内容类型：
    fulltitle：Full Title on the Cover and Title Page 封面或者扉页上的全文标题
    table_of_contents：目录/目次
    blank_page_num：空白页码
    sections: 正文内容（除fulltitl，table_of_contents，以外的文字部分）
    1）需要判断当前page有哪些内容，分别属于什么文本内容类型
    2）可能出现跨页的情况，而且目前我的样本中给出的是跨两页，后面可能有跨n页的场景 
    3）很有可能出现一整页，或者连续几页都是纯文字，无标题的情况，这种怎么判断并且特殊处理，绝不能逐页处理，也不能一起发送给llm，可能需要暂存标记，找到下一个标题或者结尾，判断这1整页或者n页属于独立内容还是某页后某section的后续大量内容
    4）大模型的输出的json一定要满足格式：
    对于fulltitle：
    {
    "fulltitle": "",
    "other_info": {}
    }
    对于table_of_contents：
    {
    "table_of_contents": {
            "page": [],
            "items": [
                {
                    "title": "",
                    "children": [
                        {
                            "title": "",
                            "children": []
                        }
                    ]
                }
            ]
        }
    }
    对于blank_page_num：
    {
        "blank_page_num": 
    }
    对于sections：
    {
    "may_be_last_page_semantics_text": "",
    "sections": [
        {
        "title": "",
        "pages": [],
        "text": "",
        "children": [
            {
            "title": "",
            "pages": [],
            "text": "",
            "children": []
            }
        ]
        }
    ]
    }
    但是对于情况2.3），我还没想好该怎么处理
