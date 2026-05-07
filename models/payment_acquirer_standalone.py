# -*- coding: utf-8 -*-

from odoo import models, fields, api


class TelebirrPaymentAcquirerStandalone(models.AbstractModel):
    """
    Standalone Telebirr Payment Integration
    
    This model provides Telebirr payment functionality without depending on
    the payment module. It can be used as a standalone payment processor.
    """
    _name = 'telebirr.payment.acquirer.standalone'
    _description = 'Telebirr Payment Acquirer Standalone'
    
    name = fields.Char('Acquirer Name', readonly=True)
    provider = fields.Char('Provider', readonly=True)
    state = fields.Selection([
        ('test', 'Test'),
        ('enabled', 'Enabled'),
        ('disabled', 'Disabled')
    ], string='State', default='test')
    
    @api.model
    def create_payment_request(self, values):
        """
        Create payment request for Telebirr
        
        Args:
            values: Dictionary with payment details
            
        Returns:
            Dictionary with payment request result
        """
        # This will call our order service
        return {
            'status': 'success',
            'message': 'Payment request created successfully'
        }
