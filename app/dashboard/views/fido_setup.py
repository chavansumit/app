import json
import secrets
import uuid

import webauthn
from flask import render_template, flash, redirect, url_for, session
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import HiddenField, validators

from app.config import RP_ID, URL
from app.dashboard.base import dashboard_bp
from app.extensions import db
from app.log import LOG
from app.models import FIDO


class FidoTokenForm(FlaskForm):
    sk_assertion = HiddenField("sk_assertion", validators=[validators.DataRequired()])


@dashboard_bp.route("/fido_setup", methods=["GET", "POST"])
@login_required
def fido_setup():
    if not current_user.can_use_fido:
        flash(
            "This feature is currently in invitation-only beta. Please send us an email if you want to try",
            "warning",
        )
        return redirect(url_for("dashboard.index"))

    fido_model = FIDO.filter_by(uuid=current_user.fido_uuid).all()

    fido_token_form = FidoTokenForm()

    # Handling POST requests
    if fido_token_form.validate_on_submit():
        try:
            sk_assertion = json.loads(fido_token_form.sk_assertion.data)
        except Exception as e:
            flash("Key registration failed. Error: Invalid Payload", "warning")
            return redirect(url_for("dashboard.index"))

        fido_uuid = session["fido_uuid"]
        challenge = session["fido_challenge"]

        fido_reg_response = webauthn.WebAuthnRegistrationResponse(
            RP_ID,
            URL,
            sk_assertion,
            challenge,
            trusted_attestation_cert_required=False,
            none_attestation_permitted=True,
        )

        try:
            fido_credential = fido_reg_response.verify()
        except Exception as e:
            LOG.error(f"An error occurred in WebAuthn registration process: {e}")
            flash("Key registration failed.", "warning")
            return redirect(url_for("dashboard.index"))

        current_user.fido_pk = str(fido_credential.public_key, "utf-8")
        current_user.fido_uuid = fido_uuid
        current_user.fido_sign_count = fido_credential.sign_count
        current_user.fido_credential_id = str(fido_credential.credential_id, "utf-8")
        db.session.commit()

        flash("Security key has been activated", "success")
        return redirect(url_for("dashboard.recovery_code_route"))

    # Prepare information for key registration process
    fido_uuid = str(uuid.uuid4())
    challenge = secrets.token_urlsafe(32)

    credential_create_options = webauthn.WebAuthnMakeCredentialOptions(
        challenge,
        "SimpleLogin",
        RP_ID,
        fido_uuid,
        current_user.email,
        current_user.name if current_user.name else current_user.email,
        False,
        attestation="none",
        user_verification="discouraged",
    )

    # Don't think this one should be used, but it's not configurable by arguments
    # https://www.w3.org/TR/webauthn/#sctn-location-extension
    registration_dict = credential_create_options.registration_dict
    del registration_dict["extensions"]["webauthn.loc"]

    for record in fido_model:
        registration_dict["excludeCredentials"].append({
            'type': 'public-key',
            'id': record.credential_id,
            'transports': ['usb', 'nfc', 'ble', 'internal'],
        })

    session["fido_uuid"] = fido_uuid
    session["fido_challenge"] = challenge.rstrip("=")

    return render_template(
        "dashboard/fido_setup.html",
        fido_token_form=fido_token_form,
        credential_create_options=registration_dict,
    )
