# Moving AI city/street data attribution

Original source: https://www.movingai.com/benchmarks/street/index.html

This directory contains original, unchanged map and scenario files for Berlin_0_256, Boston_0_256 and Denver_0_256, obtained from the official Moving AI Lab site on September 10, 2026. `provenance.json` records download URLs, original archive members, SHA-256 values and archive hashes. `selection.json` is WorldFuzz's separately authored, pre-execution selection protocol; it does not modify the source files.

Attribution: Moving AI Lab, Nathan Sturtevant. City/street maps were provided by Konstantin Yakovlev and Anton Andreychuk and discretized from building/road maps.

The data is made available under the Open Data Commons Attribution License (ODC-By) v1.0: https://opendatacommons.org/licenses/by/1-0/. The source site's data notice is at https://www.movingai.com/benchmarks/grids.html. The data is not relicensed under the WorldFuzz code's MIT license.

Please cite: Nathan Sturtevant, “Benchmarks for Grid-Based Pathfinding,” IEEE Transactions on Computational Intelligence and AI in Games, volume 4, issue 2, pages 144–148, 2012. http://web.cs.du.edu/~sturtevant/papers/benchmarks.pdf

Original format and rules: https://www.movingai.com/benchmarks/formats.html. Diagonal moves cost sqrt(2), cardinal moves cost 1, and paths cannot cut through blocked corners. Coordinates have their origin at the upper-left corner. WorldFuzz preserves the published 256×256 maps and endpoints with no scaling, cropping or obstacle removal. These selected maps use only `.` and `@`; other terrain semantics are not silently reinterpreted.
