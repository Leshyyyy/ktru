# Заглушка конвейера

`StubPipeline` возвращает детерминированные ответы с вымышленными кодами `99.99.*`. Фикстура каталога для неё:
`contracts/fixtures/fake_catalog.jsonl` (режим `PIPELINE=stub`).

```{literalinclude} ../../contracts/stub_pipeline.py
:language: python
```

Строки фикстуры каталога:

```{literalinclude} ../../contracts/fixtures/fake_catalog.jsonl
:language: json
```
