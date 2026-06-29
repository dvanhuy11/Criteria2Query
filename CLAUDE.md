# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Criteria2Query (C2Q) is an automatic cohort identification system. It converts free-text clinical trial eligibility criteria into OMOP CDM v5 cohort definitions and SQL queries. The system is a human-in-the-loop NLP pipeline: text → information extraction (NER + relation extraction + negation/temporal/value normalization) → concept mapping to OMOP vocabularies → cohort JSON → SQL.

The repository is a polyglot system with three cooperating processes:
- **`criteria2query/`** — the main Java Spring MVC web application (WAR, deployed to Tomcat). This is where the bulk of logic lives.
- **`NegationDetection/`** — a Python BioMedBERT negation-scope detector the Java app shells out to.
- **`concepthub/`** — a Python Flask service that proxies concept-mapping queries to OHDSI Usagi.

## Build & run

The Java app is built with Maven (Java 8 / `source 1.8`) and packaged as a WAR.

```bash
cd criteria2query
mvn clean package          # produces target/criteria2query.war
mvn test                   # run JUnit tests
mvn -Dtest=TestTemporalValueNormalization test   # run a single test class
```

Deploy `target/criteria2query.war` to Apache Tomcat. The Maven `<finalName>` is `criteria2query`, so it serves under `/criteria2query`.

The OHDSI dependencies (`circe`, `SqlRender`) come from the OHDSI Nexus repo declared in `pom.xml` (`repo.ohdsi.org:8085`) — builds will fail offline if these aren't cached.

Run the supporting Python services (both must be running for full pipeline functionality):

```bash
# Negation detection — invoked per-request by Java via Runtime.exec; needs the model file
# Download the biomedBERT model and place it in NegationDetection/ (see README step 3)
python -m venv .venv && source .venv/bin/activate && pip install -r venv_requirements.txt

# Concept Hub (Usagi proxy) — listens on :8081 to match GlobalSetting.concepthub
python concepthub/concepthub_server.py
```

## Configuration: `GlobalSetting.java` is the single config hub

Almost all environment-specific configuration is hardcoded as static fields in
[GlobalSetting.java](criteria2query/src/main/java/edu/columbia/dbmi/ohdsims/pojo/GlobalSetting.java).
**This is the first file to edit when setting up or changing an environment.** It contains:

- `negateDetectionFolder` / `virtualEnvFolder` — absolute paths to the `NegationDetection/` dir and the Python venv's `bin` (or `Scripts` on Windows). The Java `NegationDetection` tool builds `virtualEnvFolder + "/python"` and execs `negdetect.py`.
- `concepthub` — URL of the Flask Concept Hub (`http://localhost:8081/concepthub`).
- `databaseURL1K` / `databaseURL5pct` / `databaseUser` / `databasePassword` — PostgreSQL OMOP CDM (SynPUF) connections used for real-time cohort query execution.
- `ohdsi_api_base_url` — OHDSI WebAPI for vocabulary/ATLAS calls.
- Model file classpath locations (CRF NER model, RelEx model, negex triggers, normalization dicts) and the **domain taxonomy** (`alldomains`, `primaryEntities`, `conceptSetDomains`, `relations`, `combo`) that drives the whole extraction pipeline.

Note this file currently has machine-specific macOS absolute paths committed (and DB credentials) — be careful editing/committing it. The README documents the intended `/opt/tomcat/...` deployment values, kept as commented-out blocks at the bottom of the file.

## Architecture: the pipeline

The processing pipeline is layered as `controller → service (interface + impl) → tool`. The `tool/` package holds the NLP/ML primitives; `service/impl/` orchestrates them; `controller/` exposes Spring MVC endpoints.

**Entry points (controllers, Spring `@RequestMapping`):**
- `MainController` (`/main`) — top-level orchestration. `/runPipeline` and `/autoparse` run the whole text→cohort→SQL pipeline; `/continueParsing` resumes after user edits.
- `InformationExtractionController` (`/ie`) — `/parse` runs IE on text.
- `NlpController` (`/nlpmethod`) — JSON/SQL page transitions, `/feedback`.
- `ConceptMappingController` (`/map`) — concept set mapping, `syncConceptSets`, ignore/link terms.
- `QueryFormulationController` — builds cohort SQL.

**Core flow (`InformationExtractionServiceImpl`):**
1. **NER** — `NERTool` runs a Stanford CoreNLP CRF model (`c2q_all_model_advanced.ser.gz`) to tag entities into the domains in `GlobalSetting.alldomains` (Condition, Drug, Measurement, Temporal, Value, Negation_cue, etc.).
2. **Relation extraction** — `RelExTool` (`RelEx.model` / Weka) links attributes (Temporal, Value) to primary entities (`has_value`, `has_temporal`).
3. **Negation** — `NegationDetection` writes the input to `NegationDetection/java_python_data_transfer/`, execs the Python BioMedBERT scope detector, and reads results back via that shared file directory. `NegReTool` / `negex_triggers.txt` provide rule-based fallback.
4. **Normalization** — `TemporalNormalization` / `ValueNormalization` use the `*_normalization_dict.txt` files to turn phrases into structured comparisons.
5. **Coref / reconciliation** — `CorefTool`, `ReconTool` resolve references and reconcile document-level results.

**Concept mapping (`ConceptMapping` tool):** tagged terms are sent to the Concept Hub (`GlobalSetting.concepthub`) which queries Usagi and returns OMOP standard concepts by term + domain. `OHDSIApis` calls the OHDSI WebAPI for vocabulary lookups.

**SQL generation (`JSON2SQL`):** the cohort definition is expressed as an OHDSI circe `CohortExpression`; `JSON2SQL` uses circe's `CohortExpressionQueryBuilder` + SqlRender to emit OMOP CDM SQL. POJOs like `CdmCohort`, `CdmCriteria`, `ConceptSet`, `Criterion` mirror the circe/ATLAS cohort JSON schema.

The `model/` directory ships serialized ML models and lexicons as classpath resources (declared as Maven `<resource>` includes in `pom.xml`). The frontend is JSP pages under `src/main/webapp/` (BRAT-style annotation UI for editing extracted entities).

## Cross-process contract notes

- Java ↔ Python negation: communication is **file-based** through `NegationDetection/java_python_data_transfer/`, not stdin/stdout. The Java side execs Python with the venv interpreter; if negation "silently does nothing," check `virtualEnvFolder` and that the biomedBERT model file exists.
- Java ↔ Concept Hub: HTTP POST `{term, domain}` to port 8081. The Flask server maps C2Q domains to OMOP domains and talks to Usagi.

## Conventions / gotchas

- Java package root is `edu.columbia.dbmi.ohdsims`. New NLP primitives go in `tool/`, orchestration in `service/impl/`, data shapes in `pojo/`.
- Tests under `src/test` are mostly integration/runtime probes (`TestSend`, `TestRunTime`) rather than unit tests; `TestTemporalValueNormalization` is the closest to a true unit test.
- `_bmad/`, `_bmad-output/`, `.agents/`, `.github/`, and `paper/` are git-ignored tooling/artifact directories — not application code.
- `Usagi_v1.4.3.jar` at the repo root is the bundled OHDSI Usagi concept-mapping tool that the Concept Hub front-ends.
