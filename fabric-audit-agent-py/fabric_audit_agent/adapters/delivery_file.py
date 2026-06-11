"""File DeliveryPort: writes the envelope as pretty JSON. Port of ``adapters/delivery.file.js``.

The real delivery (``delivery_teams`` / ``ticketing``) posts to Teams / opens tickets,
implementing the same ``deliver`` contract.

``ensure_ascii=False`` matches Node's ``JSON.stringify``: envelopes carry em-dashes and other
Unicode that Node writes literally — Python would otherwise escape them to ``\\uXXXX``.
"""
import json
import os


def create_file_delivery(out_path):
    def deliver(envelope):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh, indent=2, ensure_ascii=False)
        return out_path

    return {"deliver": deliver}
