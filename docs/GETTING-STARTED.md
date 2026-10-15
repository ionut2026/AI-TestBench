# Getting started: Robot Framework and GRF

This guide assumes you have not used Robot Framework before. Start with the small counter demo.
You do not need an instrument, access to Windchill RV&S, or knowledge of the SMM product.

## 1. Understand the three pieces

### Why does this framework exist?

**GRF lets different product teams reuse the same testing foundation instead of building it again for
each product.** It is not the application you are testing, and it cannot test an unfamiliar product by itself.

Think of Robot Framework as the engine that executes steps, GRF as the shared testing toolbox and workflow,
and a product project such as SMMAutomationFramework as the instructions and tools specific to that product.
You can use Robot Framework without GRF. GRF adds conventions and checks needed for specification-linked
system testing: not just "did the test pass?", but "what required behavior did it check, is it still up to date,
and has someone reviewed it?"

| Piece | What it does | Example |
|---|---|---|
| Robot Framework | Reads test steps, executes them and records whether they passed | Run `Request Increment`, then check the value |
| General Robot Framework (GRF), this repository | Organizes projects, links tests to specifications, checks authoring rules and produces traceability reports | Explain which specification a passing test covers |
| Product automation code | Implements the actions and checks for a particular product | The demo's Python code changes and reads a counter |

A **specification** is a written statement of required behavior. The demo's specification says that requesting
an increment must increase the counter by exactly one. A test should check that statement, not merely copy
whatever the current implementation happens to do.

A **mock** is a deliberately simplified substitute for a real product. This demo's counter is just a number in
Python memory. A passing demo proves the example is connected correctly; it does not prove anything about
a real instrument.

### What does GRF contribute to SMM and future projects?

| Shared work provided by GRF | What each product project still supplies |
|---|---|
| A starting folder structure, settings file and example project through `grf init` | Its own project name, package and product-specific layout additions |
| Read-only catalog ingestion and specification-text fingerprints | Which specifications are in scope and how to access the external server |
| Brief generation to bring specification context together | Product terminology, interface information and authoring rules |
| Checks for changed specifications and test-writing problems | Tests that correctly express the product's required behavior |
| A human-review ledger and approval checks | A real reviewer who decides whether the test is correct |
| Commands to select an environment and run Robot tests | Product connections, environment preparation and safe cleanup |
| Reusable timing checks and operator/rig helpers | Product-specific actions, observations and authorized site commands |
| Reports linking specifications to test results | The actual results from testing the chosen product |

An **adapter** is the product-specific code translating a readable test action into a real interaction.
For example, a test step that requests a product operation may send an API call or message through an adapter.
GRF does not know that product's message format; the product project does.

The key benefit is reuse **without mixing products**. SMM's interfaces, service, keywords and tests stay in
SMMAutomationFramework. A different future product gets its own adapters and tests, but can use the same GRF
configuration, review rules and reporting. A generic reporting fix can then benefit both projects after each
team upgrades to and validates the release containing that fix.

### Could SMM do all this without GRF?

**Yes. GRF is a design choice, not a technical requirement.** SMM could keep its own ingestion, review,
execution and reporting code alongside its product-specific code. Robot Framework would still execute
the Robot tests, but the SMM team would maintain all the surrounding infrastructure itself.

For just one product, that can be simpler: there is no separate GRF release to install or coordinate.
For several products, shared infrastructure avoids maintaining several versions of the same solution.
The tradeoff is that GRF needs its own maintenance, documentation and compatibility checks.

For example, suppose a reporting bug labels an unreviewed test as VERIFIED. With separately implemented
reporting in four product projects, the teams may need four fixes. With GRF, the shared implementation can
be fixed once, and each consumer can adopt the corrected release after validating it. This does not
eliminate product-specific testing or make upgrades automatic.

When you run a product command such as SMM's `smm-auto`, it can call installed GRF functions internally.
You do not have to run two separate complete testing systems, first GRF and then SMM.
The product command brings together the shared infrastructure and the product's own configuration and tools.

### Does GRF draft tests for the SMM agent to complete?

**No. Currently, GRF generates briefs, not draft product tests.** A brief is an information pack for the
test author: the specification, linked information, available keyword documentation and authoring rules.
The SMM author agent or a human reads that pack and writes the `.robot` test using SMM's existing keywords.
If a necessary keyword is missing, a product maintainer must implement it; the brief does not implement it.

The exception is `grf init`: it creates a ready-made counter test to demonstrate a new project's structure.
That test is a teaching example, not an automatically generated test of your product.

The roles are:

| Participant | Responsibility |
|---|---|
| GRF | Prepares briefs, performs configured checks, coordinates execution and builds traceability reports |
| Product author agent or human author | Designs and writes the product's Robot test |
| Product reviewer agent | Independently checks the test against its specification |
| Human reviewer | Decides whether to approve the test; an agent verdict is not human approval |
| Robot Framework | Executes the keywords in the test and records results |
| Product library/adapter | Implements the product-specific actions and observations those keywords require |

### Why are agent files in both repositories?

An **agent file** is a Markdown document telling an AI assistant how to work. It is not an executable
test and does not start an AI process on its own.

GRF ships general `test-author` and `test-reviewer` instruction templates in `src\grf\templates`.
`grf init` copies them into a new product's `.github\agents` folder. The checked-in demo has matching example
copies in `examples\demo\.github\agents`; GRF does not currently have agents in its root `.github\agents`.

SMM keeps its own product-specific instructions, such as
[`smm-test-author.agent.md`](https://github.com/ionut2026/SMMAutomationFramework/blob/main/.github/agents/smm-test-author.agent.md).
That file explains SMM keywords, starting states, communication topics, observation rules, cleanup and
product-specific editing boundaries. GRF's generic templates cannot supply those details.

Both may repeat principles such as "follow the specification" and "keep human approval."
That does not mean two author agents automatically run one after the other. The product's chosen agent
uses its own instructions and GRF-generated briefs to write the test.

Templates and installed Python code have different update paths: upgrading GRF does **not** automatically
rewrite an existing product's agent files. Product maintainers must review relevant template improvements,
incorporate them deliberately and keep product-specific guidance accurate. Link to shared policy where useful
rather than maintaining unnecessary copies. No agent instruction authorizes instrument use just because an
environment is available; current owner permission is still required.

### What are Copilot skills?

A **skill** is a short instruction pack for one task, such as authoring a test or understanding a report.
An agent is a role; a skill is a procedure that the role can follow. For example, `test-author` uses
`grf-author-test` to go from a brief to a pending-review test, then run lint, drift and the mock.

GRF and every new `grf init` product contain generic skills in `.github\skills`. You can ask normally, or name one:
`Use the grf-getting-started skill and explain how to run the mock demo.` Skills do not run by themselves and do
not grant permission. See [Copilot skills](SKILLS.md) for the full list and usage instructions.

### Example: how an SMM test would use GRF

The following is an illustrative workflow, not a claim about a particular existing SMM test or keyword:

Suppose a specification says, "After request X, the product reports state Y within the specified time."

1. The SMM team selects that specification in its own scope file. GRF reads it through the external
   Windchill integration and stores it in SMM's local catalog.
2. GRF generates a brief containing the specification and its linked information. A test author uses that
   context to design a Robot test; generation of a brief does **not** automatically create a correct test.
3. The SMM team implements keywords that send request X and observe state Y. Those keywords belong to SMM
   because only that project knows how to communicate with its product.
4. The test uses those keywords and an appropriate timing assertion. GRF supplies common timing helpers,
   while SMM supplies the observed events and the limit required by the specification.
5. GRF checks the test's tags, specification fingerprint and writing rules. A human reviews whether the
   actions and assertions actually prove the required behavior.
6. The owner authorizes execution against the chosen environment. GRF starts Robot; Robot executes the
   SMM keywords; the SMM adapter interacts with the mock or actual product.
7. Robot records the executed steps. GRF joins those results with the specification catalog and review
   information so you can see which behavior was covered, which tests failed and which approvals are missing.

**The product project performs the product-specific testing; GRF supports and checks the shared process.**
Neither a framework check nor an agent's approval replaces a human review or authorized product testing.

### How do separate repositories use one framework?

A **dependency** is a package that another project installs and uses. A product installs GRF as a Python
dependency; it does not need to copy `src\grf` into its own repository.

Each product has its own `grf.toml`, libraries, catalog and Robot suites. Selecting that project tells GRF
which files and product plugin to use. A **plugin** is a small product extension that adds behavior to the
shared framework, such as product-specific readiness checks.

For a new product, the usual progression is:

1. Create a starter with `grf init` and run its counter demo to check installation.
2. Replace the counter keywords with adapters and assertions for the new product.
3. Configure the product's real specification scope, environments and required capabilities.
4. Add and review tests, then run the owner's authorized product checks.
5. Keep using an explicitly approved GRF version, upgrading only after checking product compatibility.

For an existing project such as SMM, do **not** run `grf init` over it. It already owns its product code and
configuration. Changes to that project's adapters and suites must be made in its own repository.

A **pinned version** means selecting a specific framework release rather than automatically taking the newest
one. This keeps product runs predictable: updating GRF here does not automatically update every product.
During local development a team can install this checkout directly; for controlled runs it selects an approved
release. See [Configuration](CONFIGURATION.md) and [Contributing](../CONTRIBUTING.md) for the detailed policy.

### What does deploying a build mean?

Before testing, the instrument must run the build you want to test. **Deploying** means installing a new build
of a product application on the instrument. On SMM, the applications appSMM and STI run as **containers**
(isolated, packaged programs started by Docker) on small computers inside the instrument called **ARM boards**.
**Jenkins**, the company build server, compiles each new version and keeps the result as a downloadable
**artifact**: an image tarball, a `.tar.gz` file holding the whole container image.

`grf deploy` does what a person would otherwise do by hand: download the artifact from Jenkins, copy it to the
board, stop the old container, start the new one, and check it works. If the new one does not work, it puts the
old one back automatically (**rollback**). It writes a record of what was installed so the test report can show
which build was tested. Example, a dry run that changes nothing:

```powershell
grf deploy plan --component app
```

GRF contains only the generic steps. Each product says which applications it has (SMM: appSMM and STI); the
site's Jenkins agent holds the board addresses and passwords, never a repository. **Deploying changes the
instrument, so it needs the instrument owner's authorization**; normally the owner starts the Jenkins
deployment job. Agents only explain and plan. See [Deploying builds](DEPLOYMENT.md).

### What GRF does not do

GRF does not invent specifications, automatically guarantee test coverage, understand every product's
protocol, or make all tests correct. It also does not include the Windchill server or the SMM service.
The demo is a teaching and framework-checking example, not a substitute for SMM tests.
Tests for multiple products are not collected into this core repository or run together by default.

## 2. Learn the basic vocabulary

| Term | Plain-language meaning |
|---|---|
| Test case | One named check of behavior, with actions and expected outcomes |
| Test suite | A group of tests; a `.robot` file can contain a suite, and directories can group suites |
| Keyword | A named action or check used as a test step, such as `Reset Counter` |
| Library | Code that provides keywords; the demo library is written in Python |
| Assertion | A check that fails if the observed result does not match the expected result |
| Setup / teardown | Preparation before a test / cleanup after a test |
| Variable | A named value, written as `${NAME}` in Robot, such as `${TIER}` |
| Tag | A label attached to a test, used for selection or traceability |
| Tier | GRF's name for an execution environment, such as the mock or an actual product |
| Catalog | A structured local file containing specifications and their linked information |
| Brief | A generated explanation of one specification, used when designing a test |
| Traceability | Being able to follow a requirement to its specification, test and result |
| CLI | Command-line interface: commands you type into a terminal |

Robot's keywords are not necessarily built into Robot itself. For example, `Counter Should Be` comes from
the demo's product library. A different product would provide different keywords.

## 3. Read the demo test

Open `examples\demo\robot\suites\demo\counter.robot`. Its test looks like this:

```robotframework
*** Settings ***
Library         product_automation.ProductLibrary
Test Setup      Reset Counter
Metadata        Tier    ${TIER}

*** Test Cases ***
SPEC-1 Increment Changes The Counter By One
    [Documentation]    When increment is requested, the counter increases by exactly one.
    [Tags]    SPEC-1    spechash:5404e433    review:pending
    Counter Should Be    0
    Request Increment
    Counter Should Be    1
```

Here is how to read it:

| Line | Meaning |
|---|---|
| `*** Settings ***` | Starts the configuration section of this suite |
| `Library ...` | Makes the demo's Python keywords available |
| `Test Setup Reset Counter` | Calls `Reset Counter` before each test, so every test starts with zero |
| `Metadata Tier ${TIER}` | Attaches the environment name to the suite; GRF also supplies the root run's tier |
| `*** Test Cases ***` | Starts the section containing tests |
| `SPEC-1 Increment...` | Names this test |
| `[Documentation] ...` | Describes what the test is supposed to check; it is not an executable assertion |
| `[Tags] ...` | Attaches labels; these labels do not themselves execute checks |
| `Counter Should Be 0` | Checks the starting value; this is an assertion |
| `Request Increment` | Performs the action being tested |
| `Counter Should Be 1` | Checks the outcome; this is another assertion |

Robot separates columns with **two or more spaces**, or a tab. A keyword name may itself contain single spaces.
Use the formatting shown above: `Counter Should Be    1` is a keyword followed by one argument, the expected value.
Indent steps underneath the test name.

The Python implementation is in `examples\demo\src\product_automation\ProductLibrary.py`.
Its method `request_increment` provides the readable keyword `Request Increment`; `counter_should_be` provides
`Counter Should Be`. Robot's `@keyword` decorator exposes the method as a test step. The demo's assertion raises
an error if the actual counter value differs from the expected number.

### What do the tags mean?

`SPEC-1` links the test to specification 1. `spechash:5404e433` is a short fingerprint of that specification's
text: GRF can detect when the text changes and the test needs another review. Do not copy this fingerprint
into unrelated tests or manually change it just to clear a warning.

`review:pending` means a human has not yet approved the test. The demo can run and pass with this tag;
running successfully is different from reviewing whether it checks the right behavior.
See [Testing](TESTING.md#authoring-contract) for the full review policy when you are ready.

## 4. Prepare a safe practice project

### Install after cloning

Run these commands in **Windows PowerShell 5.1 or PowerShell 7**. Git and Python 3.11 or later must already
be installed. Setup does not install those system tools or need administrator rights.

On a new machine, clone the repository first; skip the clone command if this checkout already exists:

```powershell
git clone https://github.com/ionut2026/GeneralRobotFramework.git D:\projects\GeneralRobotFramework
Set-Location D:\projects\GeneralRobotFramework
.\scripts\setup.ps1
```

**Cloning and installing are different steps.** Git copies tracked source files. It does not copy `.venv`
or execute repository scripts automatically; otherwise cloning an unfamiliar repository could run unwanted code.
The explicit setup command automates installation after you choose to run it.

Setup does the following:

1. Checks Python's version (at least 3.11).
2. Creates `.venv`, a **virtual environment** holding this project's Python packages rather than installing
   them globally. On subsequent runs it reuses that environment without deleting it. An incomplete `.venv`
   causes an error instead of being overwritten.
3. Installs GRF, Robot Framework, the parallel-test runner and development tools declared in `pyproject.toml`.
4. Checks installed dependency compatibility and prints the GRF/Robot versions and Robot's actual file location.

Expect `Setup complete` at the end. For this Windows checkout, Robot's code is normally installed under
`D:\projects\GeneralRobotFramework\.venv\Lib\site-packages\robot`. The repository tracks the declaration
`robotframework>=7.0`, not that installed code. Another machine gets its own installation.
Internet access (or access to an appropriate package source/cache) is needed to obtain dependencies.

If creating a new environment and `python` is not on PATH, supply the full path to your installed Python:

```powershell
.\scripts\setup.ps1 -Python "D:\tools\Python312\python.exe"
```

That path is an example; use your actual installation. When `.venv` already exists, setup uses its Python,
not the `-Python` argument. Explicit executable paths are checked directly; named commands are looked up
without auto-importing PowerShell modules, so a missing Python path does not trigger module discovery.
To verify the installation yourself:

```powershell
.\.venv\Scripts\python -m robot --version
.\.venv\Scripts\python -m grf --help
```

The setup script works relative to its own checkout, even if invoked from another folder. It keeps temporary
installation files under `results\setup` and package downloads under `results\pip-cache`. It does not change
global Python settings, Git hooks, PowerShell execution policy, MCP registration or product configuration.
It does not contact RV&S, start services, or execute product/instrument tests. Installed versions follow the
dependency constraints; setup is not a locked, identical-version installation across all machines.

If PowerShell blocks the script, follow your organization's approved script-execution procedure; do not
disable security policy. The equivalent manual installation is:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pip check
```

`pip` installs packages; `-e` means an editable installation using this checkout's source. `.[dev]` means
the current project plus development tools. Using the full `.venv` Python path avoids needing to activate it.

### Create the practice product

Now generate and install the practice product:

```powershell
.\.venv\Scripts\python -m grf init results\learning-counter --name "Learning Counter"
.\.venv\Scripts\python -m pip install --no-deps -e results\learning-counter
$env:GRF_PROJECT = (Resolve-Path results\learning-counter).Path
```

`python -m grf` runs GRF using that Python environment. `init` creates starter files; expect a message
beginning `Created product starter`. It refuses to overwrite a nonempty destination. If you already made
this project, do not repeat `init`; use its existing files and repeat the `GRF_PROJECT` assignment instead.

The second command makes the generated Python keywords and plugin importable. `--no-deps` avoids reinstalling
GRF, which you installed in the previous step. `GRF_PROJECT` tells GRF which product configuration to use.
This setting lasts for this PowerShell session; set it again when opening a new terminal.

Keep running the following commands from the **GRF checkout root**, not from the generated product folder.
This keeps the paths in the commands consistent.

## 5. Build the inputs, check the tests and run

Run these commands in order:

```powershell
.\.venv\Scripts\python -m grf demo-ingest
.\.venv\Scripts\python -m grf briefs
.\.venv\Scripts\python -m grf lint
.\.venv\Scripts\python -m grf drift --strict
.\.venv\Scripts\python -m grf doctor --tier mock
.\.venv\Scripts\python -m grf run --tier mock --outdir results\learning-counter-run-1
```

| Command | Why you run it | Expected result |
|---|---|---|
| `demo-ingest` | Loads fake specification data from the practice project's fixture into its catalog; no server connection | A `Demo catalog written` message |
| `briefs` | Generates the information needed to design a test for that specification | One brief written under the practice project's `generated\briefs` |
| `lint` | Checks test-writing rules without executing the test | `0 rule violation(s)` |
| `drift --strict` | Checks whether the test still matches the catalog and required traceability rules | `0 problem(s)`; a pending-review message is normal in this demo |
| `doctor --tier mock` | Checks the selected environment's prerequisites | No `FAIL` checks and `mock tier: ready` |
| `run --tier mock ...` | Executes Robot's test steps and generates reports | One test passed, zero failed, and a note that mock results are not product evidence |

The real `ingest` command reads an external Windchill server; `demo-ingest` only reads synthetic local data.
You do not need real ingestion for this exercise.

When repeating the run, change the final directory to `results\learning-counter-run-2`, then `-3`, and so on.
GRF refuses to reuse a directory containing `output.xml`, so old output cannot be mistaken for a new result.
`--outdir` is relative to the folder where you run the command.

## 6. Understand the output

Open these files in `D:\projects\GeneralRobotFramework\results\learning-counter-run-1`:

| File | What to use it for |
|---|---|
| `report.html` | Robot's summary: number of tests, pass/fail status and links to details |
| `log.html` | Robot's detailed steps: what each keyword did, messages and failure details |
| `output.xml` | Machine-readable execution results; GRF uses these to build its report |
| `traceability.html` | GRF's view: specifications, related tests, review status and drift warnings |
| `traceability.json` | The same GRF report data in a format scripts can read |

Start with `report.html`; if a test fails, look at its steps in `log.html`.
For a mock run, a green **PASS** means the executed checks succeeded against the mock.
**VERIFIED** is a stricter GRF designation requiring a product-evidence tier, current specification hashes,
human approval and no relevant specification drift. The demo must not claim VERIFIED product evidence.

## 7. Try a small failure safely

In your **generated practice project**, open:

`results\learning-counter\robot\suites\demo\counter.robot`

Change only the final assertion from `Counter Should Be    1` to `Counter Should Be    2`.
Run again with a new output directory. The test should fail with `Counter is 1, expected 2`.
This shows why assertions matter: they detect a mismatch rather than just perform an action.
Restore the expected value to `1` after the exercise. Do not change the specification fingerprint.

This exercise deliberately makes the test wrong; it is not a real product defect.
Do not use a real product or instrument for beginner experiments.

## 8. Common first-run problems

| Message or symptom | What to check |
|---|---|
| `python` is not recognized | Python must be installed and available to your terminal |
| Setup cannot find Python | Install Python 3.11 or newer or supply its actual executable path with `-Python` |
| Existing `.venv` is incomplete | Setup leaves it unchanged; investigate the existing environment before retrying |
| Setup fails during download/installation | Read the package install error and check network/package-source access; setup reports failure rather than readiness |
| PowerShell blocks `setup.ps1` | Follow approved script policy or use the manual installation commands above |
| `No module named grf` | Use `.\.venv\Scripts\python` from the checkout root and install GRF into that environment |
| No `grf.toml` found | Set `GRF_PROJECT` to the generated product's absolute path |
| Cannot import `product_automation` | Install the generated practice product using the command in step 4 |
| Destination must be new or empty | `init` already ran, or that folder contains files; use the existing starter or a new empty destination |
| Refusing to reuse existing Robot output | Choose a new `--outdir`; do not erase results just to silence this message |
| Pending human review | Expected for the starter; it is not a test execution failure |
| `lint` or `drift --strict` reports problems | Read the named file/test and diagnostic; do not remove checks or invent approval to make it pass |

You can ask for a line-by-line explanation of any diagnostic or test file.

## 9. Where to go next

Read [Architecture](ARCHITECTURE.md) to understand the folder structure, then
[Configuration](CONFIGURATION.md) to understand `grf.toml`. Use [Testing](TESTING.md) as the reference for
review, evidence and advanced validation. [Repositories](REPOSITORIES.md) explains where product and server
code belong. [Deploying builds](DEPLOYMENT.md) explains how a Jenkins build is put on the instrument's boards.

You do not need to learn mutation testing, parallel workers, SSH rig control, deployment or CI before running your first test.
**CI** means continuous integration: automated checks run by GitHub when code changes. It checks the framework,
but it cannot replace authorized real-product testing or a human's review.
