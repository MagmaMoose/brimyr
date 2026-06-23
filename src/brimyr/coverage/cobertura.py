"""Parse Cobertura XML coverage into a :class:`CoverageReport`.

Cobertura XML is emitted by ``pytest --cov --cov-report=xml`` (coverage.py),
``dotnet test --collect:"XPlat Code Coverage"`` (coverlet), ``gocover-cobertura``,
JaCoCo→Cobertura converters, and more. The shape that matters:

```xml
<coverage><packages><package><classes>
  <class filename="pkg/mod.py">
    <lines><line number="1" hits="1"/><line number="2" hits="0"/></lines>
  </class>
</classes></package></packages></coverage>
```

We read every ``<class filename=...>`` and its ``<line number= hits=>`` children.
A line's ``filename`` is recorded as-is (relative to a ``<sources>`` root, or
absolute for some tools); reconciling it with ``git diff`` paths is
:mod:`brimyr.coverage.patch`'s job, via suffix matching. Branch/condition
attributes are ignored — patch coverage is a line metric. **Pure**: parses a
string, touches no files.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from brimyr.coverage.model import CoverageBuilder, CoverageReport


class CoberturaError(ValueError):
    """The Cobertura XML could not be parsed."""


def parse_cobertura(text: str) -> CoverageReport:
    """Parse Cobertura XML text into a :class:`CoverageReport`."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CoberturaError(f"invalid Cobertura XML: {exc}") from exc

    builder = CoverageBuilder()
    for class_el in root.iter("class"):
        filename = class_el.get("filename")
        if not filename:
            continue
        for line_el in class_el.iter("line"):
            number = line_el.get("number")
            hits = line_el.get("hits")
            if number is None:
                continue
            try:
                lineno = int(number)
                hit_count = int(hits) if hits is not None else 0
            except ValueError:
                continue
            builder.record(filename, lineno, hit_count)

    return builder.build()
