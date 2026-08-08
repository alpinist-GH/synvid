# Frame-interpolation feasibility gate — failed

Candidate: FFmpeg `minterpolate` from FFmpeg `8.1.2`, using motion-compensated
interpolation (`fps=16:mi_mode=mci:mc_mode=aobmc:me_mode=bilat:vsbmc=1`) and
H.264/yuv420p output. This is a genuine motion-interpolation filter, not a
timestamp edit or duplicate-frame export.

Two distinct SynVid-owned canonical LTX clips were tested:
`82d0921e-8317-4900-88c0-347bf72691bc` and
`ebad9625-0946-4f0b-a500-5e818ab4905f`. Each source was 256x256, 8 FPS, nine
frames, and 1.125 seconds, with no audio track. Both interpolated outputs were
256x256 H.264/yuv420p at a measured 16 FPS and 15 frames, but their measured
duration was only 0.937012 seconds.

| clip | target FPS | output frames | source duration | output duration | wall time | peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `82d…91bc` | 16 | 15 | 1.125s | 0.937012s | 0.06s | 55.79 MB |
| `ebad…405f` | 16 | 15 | 1.125s | 0.937012s | 0.05s | 55.51 MB |

Direct inspection of a requested in-between frame did not show a visibly
garbled image on the first clip, but that does not compensate for the lost
duration. The clips have no audio, so audio-sync measurement is not applicable
to this pair. Cancellation cleanup and frozen-app packaging were not integrated
because the candidate already fails duration/cadence acceptance; no higher-FPS
export control is exposed.

Decision: retain `Native` as SynVid's sole FPS choice. Do not use this filter
in exports, do not add it to the frozen worker, and do not claim that metadata
or frame duplication provides interpolation.
