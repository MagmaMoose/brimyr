"""Parse JaCoCo XML coverage into a :class:`CoverageReport`.

JaCoCo is the JVM's coverage tool (Maven ``jacoco-maven-plugin``, Gradle's
``jacoco`` plugin) and it emits its **own** XML — not Cobertura. The shape that
matters:

```xml
<report name="isam3d-case">
  <package name="nl/example/isam/case">
    <sourcefile name="CaseService.java">
      <line nr="12" mi="0" ci="4" mb="0" cb="0"/>
      <line nr="13" mi="3" ci="0" mb="0" cb="0"/>
    </sourcefile>
  </package>
</report>
```

Three differences from Cobertura drive everything here:

* **Hits are instruction counts, not line hits.** ``ci`` is *covered instructions*
  on that line and ``mi`` is *missed* ones. There is no "times executed" number,
  so a line is covered iff ``ci > 0`` — a line JaCoCo only partially covered
  (``ci>0`` *and* ``mi>0``, typically a short-circuited boolean) counts as covered,
  matching how JaCoCo's own LINE counter reports it and how diff-cover treats a
  partial line.
* **The path is split across two elements.** The file path is the ``<package>``
  name joined to the ``<sourcefile>`` name — ``nl/example/isam/case/CaseService.java``
  — and is a *source-root-relative* path, so it is missing the module's
  ``backend/src/main/java`` prefix that the git diff carries.
  :mod:`brimyr.coverage.patch` reconciles that by suffix matching; nothing is
  guessed here.
* **A DOCTYPE is always present.** ``ElementTree`` skips it (no external fetch),
  which is why parsing needs no special handling — but it is the reason a JaCoCo
  file cannot be sniffed by its first bytes alone. :func:`is_jacoco` looks at the
  root element instead.

Multi-module reactors write **one report per module** (``*/target/site/jacoco/
jacoco.xml``); ingesting all of them and merging is the caller's job, exactly as
for a multi-project .NET solution. **Pure**: parses a string, touches no files.
"""

from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree as ET

from brimyr.coverage.model import CoverageBuilder, CoverageReport


class JacocoError(ValueError):
    """The JaCoCo XML could not be parsed."""


def is_jacoco(text: str) -> bool:
    """True if ``text`` is JaCoCo XML (root element ``<report>``).

    Both JaCoCo and Cobertura use the ``.xml`` extension, so the extension cannot
    tell them apart and picking wrong is **silent**: Cobertura's parser finds no
    ``<class filename=...>`` in a JaCoCo file, returns an empty report, and every
    changed Java file then contributes nothing to the denominator — a vacuous 100%
    pass over untested code. Sniffing the root element is what prevents that.
    """
    try:
        # Pull only the root element out; the rest of a multi-module report can be
        # megabytes and none of it is needed to answer this.
        stream = BytesIO(text.encode("utf-8", errors="replace"))
        for _event, element in ET.iterparse(stream, events=("start",)):
            return element.tag == "report"
    except ET.ParseError:
        return False
    return False


def parse_jacoco(text: str) -> CoverageReport:
    """Parse JaCoCo XML text into a :class:`CoverageReport`."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise JacocoError(f"invalid JaCoCo XML: {exc}") from exc

    builder = CoverageBuilder()
    for package_el in root.iter("package"):
        # A class in the default package has name="" and the path is the bare
        # filename; anything else is a '/'-joined package path already.
        prefix = (package_el.get("name") or "").strip("/")
        for source_el in package_el.iter("sourcefile"):
            name = source_el.get("name")
            if not name:
                continue
            path = f"{prefix}/{name}" if prefix else name
            for line_el in source_el.iter("line"):
                number = line_el.get("nr")
                if number is None:
                    continue
                try:
                    lineno = int(number)
                    covered_instructions = int(line_el.get("ci") or 0)
                except ValueError:
                    continue
                builder.record(path, lineno, 1 if covered_instructions > 0 else 0)

    return builder.build()
