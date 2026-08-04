"""autoresearch: a universal abstraction for autoresearch experiments.

An experiment is a folder with seed files, rules, and an objective, where the
objective is a command that prints a metric. To run it you pick an agent and
a runner. See DESIGN.md at the repository root: that document is the
contract, code follows it.
"""

__version__ = "0.2.0"
