# Distribution notices

WorldFuzz code and authored documentation: MIT, root LICENSE. Public release is
proposed as a new snapshot; commit history, private workspaces and client traces
are excluded. Owning this GitHub account does not establish third-party rights.

| Included material | Source and distribution basis |
| --- | --- |
| Original Berlin/Boston/Denver map/scenario files | Moving AI Lab, Nathan Sturtevant; city maps by Konstantin Yakovlev and Anton Andreychuk. ODC-By 1.0, not WorldFuzz MIT. Attribution, URLs and original hashes in worldfuzz/benchmark_data/README.md and provenance.json. [License](https://opendatacommons.org/licenses/by/1-0/) and [source notice](https://www.movingai.com/benchmarks/grids.html) |
| PythonRobotics controller code | MIT. Retained PYTHONROBOTICS_LICENSE and pinned provenance in worldfuzz/vendor |
| Can XML/model metadata supplement | Extracted from the pinned robomimic dataset, whose [card declares MIT](https://huggingface.co/datasets/robomimic/robomimic_datasets/blob/74fa018461f479cd9fd15b924a16103012096203/README.md). robomimic notice and robosuite 1.5.1 MIT notice supplied in licenses/. robosuite notes partial MuJoCo implementation; Apache-2.0 text included conservatively. XML mesh path strings are upstream references, not bundled meshes. HDF5, recordings, images, meshes and model weights are not bundled |
| MCP Registry schema | Upstream MIT notice in integrations/mcp/schema/LICENSE; exact schema version and SHA in that directory |
| Trajectory extension kit | WorldFuzz-authored numeric example, MIT. Not a third-party robot benchmark |

Full robomimic/robosuite notices were absent in rc5. rc6 remedies their omission;
rc5 archives remain private historical evidence and are not the proposed public
download. sources.json pins downloaded notice URLs and bytes. ODC attribution is
also present inside each independent core distribution. The separately installed
Python dependencies retain their own notices; their wheels are not vendored here.

No original HDF5, external simulator meshes, MCAP binary sample, model weights,
private agent conversations or credential package is proposed for distribution.
Public data availability is not consent to relicense it. Optional dataset users
must follow the source terms; organization membership cannot expand those rights.

No NVIDIA weights are included. The proposed remote model is exposed by Nebius
as NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 under OpenMDW v1.1; model and service
terms apply separately to the later approved inference. This notice does not
certify ownership or legal eligibility for the competition.
