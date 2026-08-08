# FLUX.1-schnell Stage 3 gate — passed for source and provider routing

Checkpoint: `black-forest-labs/FLUX.1-schnell` at
`741f7c3ce8b383c54771c7003378a50191e9efe9` (Apache-2.0). The SynVid-owned
snapshot was verified before the MPS measurement.

The selected bfloat16 profile produced a visually coherent 512x512 RGB PNG at
four steps, guidance scale 0, and seed 42. Its direct smoke measurement was
48.45 seconds with a 33.72 GB peak MPS allocation. FLUX is now one of only two
worker/UI model IDs accepted by Rust and Python: `flux-schnell` and
`ltx-video`; neither Wan model is registered because their visual gates failed.

The rebuilt relocated worker returned a status payload advertising FLUX's
measured image profile (512x512, four steps, bfloat16-derived settings), and a
real generation through that bundled worker finalized a valid, directly
inspected 512x512 RGB PNG (`d3024aa2-7a20-48ef-b401-1ae10e9e3038`).

The bundled run did not emit its expected terminal event before the worker
exited: PyTorch's `multiprocessing.resource_tracker` survived as an orphan and
kept the smoke harness pipe open. This does not invalidate the FLUX media or
provider-routing gate, but it is a pre-existing worker lifecycle/packaging
defect that keeps the Stage 2 forced-kill/restart acceptance work open. It must
be fixed before release packaging is accepted.
