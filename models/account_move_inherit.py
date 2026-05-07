# -*- coding: utf-8 -*-
"""
Account Move (Invoice) inheritance for Telebirr payment integration
"""

from odoo import models, api, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_telebirr_payment_wizard(self):
        """
        Open Telebirr payment wizard for this invoice
        """
        self.ensure_one()
        
        # Check if invoice is in valid state for payment
        if self.state != 'posted':
            raise UserError(_("Invoice must be posted to request payment"))
        
        if self.payment_state == 'paid':
            raise UserError(_("Invoice is already paid"))
        
        # Create payment wizard
        wizard = self.env['telebirr.payment.wizard'].create({
            'res_model': 'account.move',
            'res_id': self.id,
            'partner_id': self.partner_id.id,
            'amount': self.amount_residual,
            'currency_id': self.currency_id.id,
            'payment_title': _('Payment for Invoice %s') % self.name,
            'customer_phone': self.partner_id.phone or self.partner_id.mobile,
        })
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Telebirr Payment'),
            'res_model': 'telebirr.payment.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }
