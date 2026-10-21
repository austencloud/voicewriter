# Third-party software

VoiceWriter is licensed under the GNU General Public License v3 (see `LICENSE`),
because it is built on PyQt6, which is GPL-3.0.

The installer contains only VoiceWriter's own code and the `uv` installer tool. Everything
below is downloaded during setup from the Python Package Index (pypi.org), or on first
run from Hugging Face, straight onto your PC, under each project's own license.

| Component | Used for | License |
| --- | --- | --- |
| [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) | The bar and its menu | GPL-3.0 |
| Qt 6 (`pyqt6-qt6`) | Window toolkit under PyQt6 | LGPL-3.0 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Speech recognition, including the bundled Silero VAD model | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | Runs Whisper on the GPU | MIT |
| [Whisper models](https://huggingface.co/Systran) (`large-v3`, `small.en`, converted by SYSTRAN) | The speech models, downloaded on first run (about 3 GB) | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Detects when you are speaking | MIT |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Runs the Silero VAD model | MIT |
| [NVIDIA cuBLAS, cuDNN, NVRTC](https://pypi.org/project/nvidia-cudnn-cu12/) | GPU math libraries | NVIDIA proprietary, redistributable; installed from PyPI under NVIDIA's license |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | Microphone input | MIT |
| [python-soxr](https://github.com/dofuuz/python-soxr) | Resampling microphone audio | LGPL-2.1-or-later |
| [NumPy](https://numpy.org) | Audio math | BSD-3-Clause and others |
| [PyAV](https://github.com/PyAV-Org/PyAV) | Audio decoding (faster-whisper dependency) | BSD-3-Clause |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub), tokenizers, hf-xet | Model download and tokenizing | Apache-2.0 |
| [uv](https://github.com/astral-sh/uv) | Installs Python and the packages during setup (bundled in the installer) | MIT or Apache-2.0 |
| Python | Runtime, installed by uv during setup | PSF-2.0 |

The full pinned list is in `requirements.txt`; every other package there is MIT, BSD,
Apache-2.0, MPL-2.0 or PSF licensed.
