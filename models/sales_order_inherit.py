# -*- coding: utf-8 -*-
"""
Sales Order inheritance for Telebirr payment integration
"""

from odoo import models, api, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_telebirr_payment_wizard(self):
        """
        Open Telebirr payment wizard for this sales order
        """
        self.ensure_one()
        
        # Check if sales order is in valid state for payment
        if self.state not in ['sale', 'done']:
            raise UserError(_("Sales order must be confirmed or done to request payment"))
        
        # Create payment wizard
        wizard = self.env['telebirr.payment.wizard'].create({
            'res_model': 'sale.order',
            'res_id': self.id,
            'partner_id': self.partner_id.id,
            'amount': self.amount_total,
            'currency_id': self.currency_id.id,
            'payment_title': _('Payment for Sales Order %s') % self.name,
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
