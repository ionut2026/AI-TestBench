[project]
name = __NAME_JSON__
product = __NAME_JSON__
spec_prefix = "__SPEC_PREFIX__"
plugin = "product_automation.plugin:DemoPlugin"

[framework]
version = "0.4.1"

[paths]
scope = "catalog/demo.scope.toml"
catalog_dir = "catalog"
reviews = "catalog/reviews.toml"
suites = ["robot/suites"]
environments = "robot/environments"
results = "results"
briefs = "generated/briefs"
brief_rules = "catalog/brief-rules.md"
brief_target = "robot/suites/{scope}/{area}.robot"
keyword_docs = ["product_automation.ProductLibrary"]
mutants = "catalog/mutants.toml"

[tiers.mock]
capabilities = []
evidence = false
parallel = true
