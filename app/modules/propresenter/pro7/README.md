# Pro7 file definitions

`pro7.desc` is a protobuf descriptor set compiled from the community `.proto` files in
[greyshirtguy/ProPresenter7-Proto](https://github.com/greyshirtguy/ProPresenter7-Proto) (MIT, see `LICENSE`),
`autogen-proto` branch, commit ab67c6ad9c52e7dacba191044239296330fa309e (ProPresenter 21/22 era).
`ppfile.py` builds the message classes from it at run time, so no generated code is tied to a protobuf version.

Rebuild: `python -m grpc_tools.protoc -I autogen-proto --include_imports --descriptor_set_out=pro7.desc autogen-proto/*.proto`.

`lyric_slide.bin` is one lyric slide (a `Cue`) exported from ProPresenter 22 with its text emptied; `ppfile.py` clones it for every slide it writes.
