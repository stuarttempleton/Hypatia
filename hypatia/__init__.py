"""Hypatia — curate Project Gutenberg into themed, delimited text shelves.

A build-time toolchain that produces the Pinakes data set: a manifest + catalog
(`index.txt`) and a set of themed book shelves (`shelf_NN.txt`). The output format
is defined by CONTRACT.md and knows nothing about any particular consumer — VRChat
is merely the first (and strictest) client.

Pipeline (see build.py): catalog -> popularity -> grouping -> fetch -> text -> emit.
"""

__version__ = "0.1.0"
