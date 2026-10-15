[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = __PACKAGE_JSON__
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["general-robot-framework==0.4.3"]

[tool.setuptools.packages.find]
where = ["src"]
