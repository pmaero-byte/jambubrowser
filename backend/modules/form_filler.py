"""
Autonomous Form Filler
=======================
Detects form fields on web pages and fills them using
credentials from the encrypted vault. Handles:
- Login forms (username/email + password)
- Registration forms
- Search forms
- Survey/checkout forms
"""

import re
import json
import asyncio
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse
from dataclasses import dataclass, field

from backend.core.database import get_db_cursor
from backend.core.vault import get_vault


@dataclass
class FormField:
    """A detected form field."""
    name: str
    field_type: str  # text, email, password, select, checkbox, radio, textarea, submit
    selector: str
    label: str = ""
    placeholder: str = ""
    required: bool = False
    value: str = ""
    options: List[str] = field(default_factory=list)


@dataclass 
class DetectedForm:
    """A complete form detected on a page."""
    url: str
    form_selector: str
    fields: List[FormField]
    action_url: str = ""
    method: str = "POST"
    has_submit: bool = True
    confidence: float = 0.0


class FormDetector:
    """
    Detects and classifies form fields on web pages.
    Uses regex patterns on HTML to identify form structures.
    """

    FORM_PATTERN = re.compile(
        r'<form\b[^>]*?>', re.I | re.DOTALL
    )
    INPUT_PATTERN = re.compile(
        r'<input\b[^>]*?/?>', re.I
    )
    LABEL_PATTERN = re.compile(
        r'<label\b[^>]*?>(.*?)</label>', re.I | re.DOTALL
    )
    SELECT_PATTERN = re.compile(
        r'<select\b[^>]*?>(.*?)</select>', re.I | re.DOTALL
    )
    OPTION_PATTERN = re.compile(
        r'<option\b[^>]*?>(.*?)</option>', re.I
    )
    TEXTAREA_PATTERN = re.compile(
        r'<textarea\b[^>]*?>(.*?)</textarea>', re.I | re.DOTALL
    )
    BUTTON_PATTERN = re.compile(
        r'<button\b[^>]*?>', re.I
    )

    @staticmethod
    def _extract_attr(tag: str, attr: str) -> str:
        m = re.search(rf'\b{attr}\s*=\s*["\']([^"\']*)["\']', tag, re.I)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_action(form_tag: str) -> str:
        return FormDetector._extract_attr(form_tag, 'action')

    @staticmethod
    def _extract_id(tag: str) -> str:
        return FormDetector._extract_attr(tag, 'id')

    def detect_forms(self, html: str, page_url: str) -> List[DetectedForm]:
        """Detect all forms in HTML content."""
        forms = []
        form_matches = self.FORM_PATTERN.finditer(html)

        for form_match in form_matches:
            form_start = form_match.start()
            form_end = html.find('</form>', form_start)
            if form_end == -1:
                form_end = len(html)

            form_html = html[form_start:form_end]
            action_url = self._extract_action(form_match.group(0)) or page_url
            fields = self._extract_fields(form_html)

            if fields:
                has_submit = any(f.field_type == 'submit' for f in fields)
                forms.append(DetectedForm(
                    url=page_url,
                    form_selector=self._guess_form_selector(form_html),
                    fields=fields,
                    action_url=action_url,
                    has_submit=has_submit,
                    confidence=self._compute_confidence(fields),
                ))

        return forms

    def _extract_fields(self, form_html: str) -> List[FormField]:
        """Extract form fields from form HTML fragment."""
        fields = []
        seen_names = set()

        # Extract labels for later matching
        labels = {}
        for m in self.LABEL_PATTERN.finditer(form_html):
            tag = m.group(0)
            for_id = self._extract_attr(tag, 'for')
            label_text = re.sub(r'<[^>]+>', '', m.group(1) or '').strip()
            if for_id:
                labels[for_id] = label_text

        # Extract inputs
        for m in self.INPUT_PATTERN.finditer(form_html):
            tag = m.group(0)
            input_type = (self._extract_attr(tag, 'type') or 'text').lower()
            name = self._extract_attr(tag, 'name')
            placeholder = self._extract_attr(tag, 'placeholder')
            value = self._extract_attr(tag, 'value')
            is_required = 'required' in tag.lower()

            if name in seen_names:
                continue
            seen_names.add(name)

            label = labels.get(name, placeholder or name)
            field_type = self._classify_field(input_type, name, label, placeholder)
            selector = self._build_selector(name, input_type)

            fields.append(FormField(
                name=name, field_type=field_type, selector=selector,
                label=label, placeholder=placeholder,
                required=is_required, value=value,
            ))

        # Extract selects
        for m in self.SELECT_PATTERN.finditer(form_html):
            tag = m.group(0)
            name = self._extract_attr(tag, 'name')
            select_html = m.group(1)
            options = []

            for om in self.OPTION_PATTERN.finditer(select_html):
                opt_tag = om.group(0)
                opt_text = re.sub(r'<[^>]+>', '', om.group(1) or '').strip()
                opt_value = self._extract_attr(opt_tag, 'value') or opt_text
                options.append(opt_text)

            if name in seen_names:
                continue
            seen_names.add(name)

            fields.append(FormField(
                name=name, field_type='select',
                selector=f'select[name="{name}"]',
                label=labels.get(name, name), options=options,
            ))

        for m in self.BUTTON_PATTERN.finditer(form_html):
            tag = m.group(0)
            btn_type = (self._extract_attr(tag, 'type') or 'submit').lower()
            if btn_type != 'submit':
                continue
            name = self._extract_attr(tag, 'name') or '__submit_btn__'
            if name in seen_names:
                continue
            seen_names.add(name)
            fields.append(FormField(
                name=name, field_type='submit',
                selector='button[type="submit"]',
                label='Submit',
            ))
            break

        return fields

    def _classify_field(self, input_type: str, name: str, 
                         label: str, placeholder: str) -> str:
        """Classify a field's semantic type from its attributes."""
        combined = f"{name} {label} {placeholder}".lower()

        if input_type in ('email', 'password', 'submit', 'checkbox', 'radio', 'hidden'):
            return input_type

        if any(kw in combined for kw in ('email', 'e-mail', 'mail')):
            return 'email'
        if any(kw in combined for kw in ('password', 'passwd', 'pwd')):
            return 'password'
        if any(kw in combined for kw in ('username', 'user', 'login', 'account')):
            return 'username'
        if any(kw in combined for kw in ('name', 'fullname', 'full_name')):
            return 'name'
        if any(kw in combined for kw in ('phone', 'mobile', 'tel')):
            return 'phone'
        if any(kw in combined for kw in ('address', 'street')):
            return 'address'
        if any(kw in combined for kw in ('search', 'query', 'q')):
            return 'search'

        return 'text'

    def _build_selector(self, name: str, input_type: str) -> str:
        if name:
            return f'input[name="{name}"]'
        if input_type and input_type != 'text':
            return f'input[type="{input_type}"]'
        return 'input[type="text"]'

    def _guess_form_selector(self, form_html: str) -> str:
        id_match = re.search(r'id=["\']([^"\']*)["\']', form_html, re.I)
        if id_match:
            return f'form#{id_match.group(1)}'
        class_match = re.search(r'class=["\']([^"\']*)["\']', form_html, re.I)
        if class_match:
            return f'form.{class_match.group(1).split()[0]}'
        return 'form'

    def _compute_confidence(self, fields: List[FormField]) -> float:
        if not fields:
            return 0.0
        identifiable = sum(1 for f in fields if f.field_type != 'text')
        return min(1.0, identifiable / len(fields))


class FormFiller:
    """
    Matches detected forms with vault credentials and generates
    fill instructions (selectors + values).
    """

    def __init__(self):
        self._detector = FormDetector()
        self._vault = get_vault()

    def detect_and_match(self, html: str, page_url: str) -> Dict:
        """
        Detect forms on a page and match with stored credentials.

        Returns:
            dict with detected forms and matched credentials
        """
        forms = self._detector.detect_forms(html, page_url)
        parsed = urlparse(page_url)
        domain = parsed.hostname or ''

        # Vault may be locked (its default state) — form detection still
        # works, we just can't match credentials. Degrade gracefully.
        matched_cred: Optional[dict] = None
        try:
            matched_cred = self._vault.find_best_credential(page_url)
        except PermissionError:
            pass

        results = []
        for form in forms:
            # Classify the form type
            form_type = self._classify_form(form)

            # Generate fill instructions
            fill_data = {}
            if matched_cred:
                fill_data = self._generate_fill_data(form, matched_cred)

            results.append({
                'form_selector': form.form_selector,
                'form_type': form_type,
                'action_url': form.action_url,
                'confidence': form.confidence,
                'field_count': len(form.fields),
                'has_submit': form.has_submit,
                'fields': [
                    {
                        'name': f.name,
                        'type': f.field_type,
                        'selector': f.selector,
                        'label': f.label,
                        'required': f.required,
                        'value': fill_data.get(f.selector, f.value),
                    }
                    for f in form.fields
                ],
                'matched_credential': {
                    'domain': matched_cred['domain'],
                    'username': matched_cred['username'],
                } if matched_cred else None,
                'auto_fillable': matched_cred is not None,
            })

        return {
            'url': page_url,
            'domain': domain,
            'forms_found': len(forms),
            'forms': results,
        }

    def _classify_form(self, form: DetectedForm) -> str:
        """Classify the semantic type of a form."""
        field_types = [f.field_type for f in form.fields]
        field_names = [f.name.lower() for f in form.fields]

        has_password = 'password' in field_types
        has_email = 'email' in field_types
        has_username = 'username' in field_types or any(
            'username' in n for n in field_names
        )
        has_name = 'name' in field_types or any(
            word in n for n in field_names for word in ('fullname', 'full_name', 'name')
        )

        # Registration: password + name field (with or without email)
        if has_password and has_name:
            return 'registration'
        # Login: password + email/username
        if has_password and (has_email or has_username):
            return 'login'
        if has_password and not has_email:
            return 'registration'
        if any(f.field_type == 'search' for f in form.fields):
            return 'search'
        if any(f.field_type in ('phone', 'address') for f in form.fields):
            return 'checkout'

        return 'generic'

    def _generate_fill_data(self, form: DetectedForm, 
                             credential: dict) -> Dict[str, str]:
        """Generate selector→value mappings for form filling."""
        fill_data = {}

        for field in form.fields:
            if field.field_type == 'email':
                fill_data[field.selector] = credential.get('username', '')
                fill_data[f'input[type="email"]'] = credential.get('username', '')
            elif field.field_type == 'username':
                fill_data[field.selector] = credential.get('username', '')
            elif field.field_type == 'password':
                fill_data[field.selector] = credential.get('password', '')
                fill_data[f'input[type="password"]'] = credential.get('password', '')
            elif field.field_type == 'name':
                metadata = credential.get('metadata', {})
                fill_data[field.selector] = metadata.get('full_name', credential.get('username', ''))

        return fill_data

    def generate_js_fill_script(self, fill_data: Dict[str, str], 
                                  submit: bool = True) -> str:
        """Generate JavaScript to fill form fields and optionally submit."""
        lines = []
        for selector, value in fill_data.items():
            escaped_value = value.replace("'", "\\'").replace('\n', '\\n')
            lines.append(
                f"const el=document.querySelector('{selector}');"
                f"if(el){{el.value='{escaped_value}';"
                f"el.dispatchEvent(new Event('input',{{bubbles:true}}));"
                f"el.dispatchEvent(new Event('change',{{bubbles:true}}));}}"
            )

        if submit and lines:
            lines.append(
                "const submitBtn=document.querySelector('button[type=\"submit\"],"
                "input[type=\"submit\"],form button:last-of-type');"
                "if(submitBtn)submitBtn.click();"
            )

        return "(async()=>{" + ' '.join(lines) + "})()"


# ── Module-level async entry points (called by /forms/detect & /forms/fill-script) ─


async def detect_and_classify(url: str) -> dict:
    """Fetch a page, detect forms, and match with vault credentials."""
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    ) as client:
        resp = await client.get(url)
        html = resp.text

    filler = get_form_filler()
    return filler.detect_and_match(html, url)


async def generate_fill_js(url: str) -> dict:
    """Generate JavaScript to fill a form with vault credentials."""
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    ) as client:
        resp = await client.get(url)
        html = resp.text

    filler = get_form_filler()
    result = filler.detect_and_match(html, url)

    scripts = []
    for form in result.get("forms", []):
        if form.get("auto_fillable"):
            fill_data = {f["selector"]: f["value"] for f in form.get("fields", [])}
            js = filler.generate_js_fill_script(fill_data, submit=True)
            scripts.append({
                "form_selector": form.get("form_selector", ""),
                "form_type": form.get("form_type", "generic"),
                "script": js,
            })

    return {
        "url": url,
        "forms_found": result.get("forms_found", 0),
        "scripts": scripts,
    }


# ---- Module-level singleton ----

_filler: Optional[FormFiller] = None


def get_form_filler() -> FormFiller:
    global _filler
    if _filler is None:
        _filler = FormFiller()
    return _filler
