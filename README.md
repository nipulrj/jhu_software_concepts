# jhu_software_concepts

Coursework for JHU EN.605.256, Modern Software Concepts in Python. The course covers advanced,
workforce-oriented Python topics across web programming, databases, concurrent programming,
cloud computing, machine learning, cyber security, and quantum computing, using tools such as
Flask, Docker, Git, AWS, and PyCharm/VSCode.

Each module in this repository holds a self-contained assignment for the course.

| Module | What it is |
|---|---|
| [`module_1`](module_1/) | A personal site in Flask: blueprints, templates, static assets. |
| [`module_2`](module_2/) | Scraping The Grad Cafe, cleaning the results, and standardizing program names with a local LLM. |
| [`module_3`](module_3/) | Loading that data into PostgreSQL, answering eleven analytical questions in raw SQL and through the SQLAlchemy ORM, and serving them from a Flask page. |
| [`module_4`](module_4/) | The Module 3 application made testable, tested and documented: 489 pytest tests at 100% coverage, GitHub Actions CI, and [Sphinx documentation](https://jhu-software-concepts-gradcafe-analytics-nipulrj.readthedocs.io/). |

Continuous integration for `module_4` runs from
[`.github/workflows/tests.yml`](.github/workflows/tests.yml): it starts a PostgreSQL
service container, runs the full marked test suite with coverage, and builds the
documentation with warnings as errors. A byte-identical copy is kept at
[`module_4/.github/workflows/tests.yml`](module_4/.github/workflows/tests.yml) so
that module reads on its own; a test fails if the two drift apart.
