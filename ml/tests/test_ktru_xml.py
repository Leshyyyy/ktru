import zipfile

from contracts.ml_contracts import CatalogRow

from ml.ktru_xml import build

# Искусственная позиция в формате выгрузки ЕИС nsiKTRUNew (не реальная позиция каталога).
XML = """<export xmlns="http://zakupki.gov.ru/oos/export/1" xmlns:oos="http://zakupki.gov.ru/oos/types/1"><nsiKTRUs>
<oos:position><oos:data>
  <oos:code>77.77.09.000-00000001</oos:code><oos:version>{version}</oos:version>
  <oos:name>Изделие тестовое
</oos:name>
  <oos:OKPD2><oos:code>77.77.09.000</oos:code></oos:OKPD2>
  <oos:status>{status}</oos:status><oos:isTemplate>false</oos:isTemplate>
  <oos:OKEIs><oos:OKEI><oos:code>796</oos:code><oos:name>Штука</oos:name></oos:OKEI></oos:OKEIs>
  <oos:characteristics>
    <oos:characteristic><oos:name>Длина</oos:name><oos:actual>true</oos:actual><oos:isRequired>true</oos:isRequired>
      <oos:values><oos:value><oos:OKEI><oos:name>Миллиметр</oos:name></oos:OKEI><oos:rangeSet><oos:valueRange>
        <oos:minMathNotation>greaterOrEqual</oos:minMathNotation><oos:min>10</oos:min>
        <oos:maxMathNotation>less</oos:maxMathNotation><oos:max>20</oos:max>
      </oos:valueRange></oos:rangeSet></oos:value></oos:values></oos:characteristic>
    <oos:characteristic><oos:name>Цвет</oos:name><oos:actual>true</oos:actual><oos:isRequired>false</oos:isRequired>
      <oos:values><oos:value><oos:qualityDescription>Белый</oos:qualityDescription></oos:value></oos:values>
    </oos:characteristic>
    <oos:characteristic><oos:name>Устаревшая</oos:name><oos:actual>false</oos:actual></oos:characteristic>
  </oos:characteristics>
</oos:data></oos:position>
</nsiKTRUs></export>"""


def _zip(path, name, **kw):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name.replace(".zip", ""), XML.format(**kw))


def test_full_export_then_increment(tmp_path):
    _zip(tmp_path / "nsiKTRUNew_all_actual_20261010020003_001.xml.zip", "a.xml.zip", version=1, status="ACTIVE")
    _zip(tmp_path / "nsiKTRUNew_inc_not-actual_20261011000000_20261012000000_001.xml.zip", "b.xml.zip",
         version=2, status="TERMINATED")
    rows = build(tmp_path)
    row = rows["77.77.09.000-00000001"]
    CatalogRow.model_validate(row)
    assert row["status"] == "archived"  # инкремент с более поздней версией заменил полную выгрузку
    assert row["name"] == "Изделие тестовое" and row["unit"] == "Штука" and row["okpd2_code"] == "77.77.09.000"
    length, color = row["characteristics"]
    assert length == {"name": "Длина", "values": ["≥ 10 и < 20"], "required": True, "unit": "Миллиметр", "kind": "range"}
    assert color["values"] == ["Белый"] and len(row["characteristics"]) == 2
