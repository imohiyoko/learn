# refs/

公式の過去問・解答例・採点講評、シラバスなどを置く場所です。このフォルダの中身（このREADME以外）はgitに含まれません。

おすすめの置き方：

```
refs/
├ past/
│  ├ r06a-q1.pdf          # 問題冊子（該当問だけ切り出してもOK）
│  ├ r06a-answer.pdf      # 公式解答例
│  └ r06a-comment.pdf     # 採点講評
└ syllabus/
   └ syllabus.pdf
```

`/import-past` に問題番号を伝えると、ここから読み取って `questions/` と `rubrics/` に取り込みます。
