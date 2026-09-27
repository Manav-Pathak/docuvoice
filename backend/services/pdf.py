from __future__ import annotations

from io import BytesIO

import pymupdf


class PDFGenerationService:
    def generate(self, schema, form) -> bytes:
        document = pymupdf.open()
        page = document.new_page(width=595, height=842)
        navy = (0.07, 0.15, 0.24)
        teal = (0.02, 0.55, 0.49)
        page.draw_rect((0, 0, 595, 90), color=navy, fill=navy)
        page.insert_text((42, 42), "DOCUVOICE", fontsize=11, color=(0.75, 0.95, 0.91))
        page.insert_text((42, 70), schema.title, fontsize=19, color=(1, 1, 1))

        y = 120
        current_section = None
        for definition in schema.fields:
            if definition.section != current_section:
                current_section = definition.section
                if y > 735:
                    page = document.new_page(width=595, height=842)
                    y = 55
                page.insert_text(
                    (42, y), current_section.upper(), fontsize=9, color=teal
                )
                y += 23
            value = form.fields[definition.key].value or "Not provided"
            page.insert_text(
                (42, y), definition.label, fontsize=8, color=(0.35, 0.4, 0.45)
            )
            page.insert_textbox(
                (190, y - 10, 550, y + 22), value, fontsize=10, color=navy
            )
            page.draw_line(
                (42, y + 16), (550, y + 16), color=(0.88, 0.9, 0.91), width=0.5
            )
            y += 36
            if y > 780:
                page = document.new_page(width=595, height=842)
                y = 55
                current_section = None

        output = BytesIO()
        document.save(output)
        document.close()
        return output.getvalue()
