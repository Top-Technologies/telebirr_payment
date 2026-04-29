# -*- coding: utf-8 -*-

import json
from datetime import datetime, timedelta
import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class TelebirrConfig(models.Model):
    """
    Telebirr Payment Configuration
    
    This model stores all configuration needed to connect with Telebirr API.
    Each company can have multiple configurations (test/production).
    """
    _name = 'telebirr.config'
    _description = 'Telebirr Payment Configuration'
    _order = 'sequence, name'
    
    # Basic Information
    name = fields.Char('Configuration Name', required=True,
                     help='Descriptive name for this configuration')
    sequence = fields.Integer('Sequence', default=10,
                            help='Order of display in lists')
    environment = fields.Selection([
        ('test', 'Test Environment'),
        ('prod', 'Production Environment')
    ], string='Environment', required=True, default='test',
       help='Test environment uses Telebirr sandbox, Production uses live system')
    
    # API Credentials
    fabric_app_id = fields.Char('Fabric App ID', required=True,
                               help='X-APP-Key provided by Telebirr (Fabric App ID)')
    app_secret = fields.Char('App Secret', required=True, encrypted=True,
                           size=256,
                           help='App Secret provided by Telebirr for authentication')
    merchant_app_id = fields.Char('Merchant App ID', required=True, size=64,
                                 help='Application ID allocated to merchant by Telebirr')
    merchant_code = fields.Char('Merchant Code', required=True, size=16,
                              help='Short code registered by merchant with Telebirr')
    
    # RSA Keys for Signature
    private_key = fields.Text('Private Key', required=True, encrypted=True,
                            help='RSA private key for signing API requests (PEM format)')
    public_key = fields.Text('Public Key', help='RSA public key for webhook verification (PEM format)')
    
    # URLs
    webhook_url = fields.Char('Webhook URL', required=True,
                             help='URL where Telebirr sends payment notifications')
    redirect_url = fields.Char('Success Redirect URL',
                             help='URL where customers are redirected after successful payment')
    
    # Settings
    timeout_express = fields.Integer('Payment Timeout (minutes)', default=120,
                                  help='Maximum time for payment completion (1-120 minutes)')
    active = fields.Boolean('Active', default=True,
                          help='Only active configurations can be used for payments')
    
    # Company and Access
    company_id = fields.Many2one('res.company', 'Company', required=True,
                                 default=lambda self: self.env.company,
                                 help='Company this configuration belongs to')
    
    # Computed Fields
    base_url = fields.Char('Base API URL', compute='_compute_urls', store=True,
                          help='Telebirr API endpoint URL')
    web_url = fields.Char('Web Checkout URL', compute='_compute_urls', store=True,
                         help='Telebirr web checkout URL')
    
    # Status Information
    last_test_time = fields.Datetime('Last Test Time', readonly=True)
    last_test_result = fields.Text('Last Test Result', readonly=True)
    is_connection_ok = fields.Boolean('Connection OK', compute='_compute_connection_status',
                                   store=True, help='Last connection test result')
    
    @api.depends('environment')
    def _compute_urls(self):
        """
        Compute API URLs based on environment
        
        This method sets the correct Telebirr endpoints:
        - Test: developerportal.ethiotelebirr.et
        - Production: telebirrappcube.ethiomobilemoney.et
        """
        for config in self:
            if config.environment == 'test':
                config.base_url = 'https://developerportal.ethiotelebirr.et:38443/apiaccess/payment/gateway'
                config.web_url = 'https://developerportal.ethiotelebirr.et:38443/payment/web/paygate?'
            else:  # prod
                config.base_url = 'https://telebirrappcube.ethiomobilemoney.et:38443/apiaccess/payment/gateway'
                config.web_url = 'https://telebirrappcube.ethiomobilemoney.et:38443/payment/web/paygate?'
    
    @api.depends('last_test_time')
    def _compute_connection_status(self):
        """
        Compute connection status based on last test
        
        Connection is considered OK if:
        - Never tested (False)
        - Last test was successful (True)
        - Last test failed more than 24 hours ago (False, but test again)
        """
        for config in self:
            if not config.last_test_time:
                config.is_connection_ok = False
                continue
            
            # Check if last test was successful
            if config.last_test_result and 'success' in config.last_test_result.lower():
                config.is_connection_ok = True
            else:
                # If last test failed, check if it's older than 24 hours
                time_diff = datetime.now() - config.last_test_time
                if time_diff > timedelta(hours=24):
                    config.is_connection_ok = False  # Should test again
    
    @api.constrains('timeout_express')
    def _check_timeout_express(self):
        """
        Validate timeout value
        
        Telebirr requires timeout between 1-120 minutes
        """
        for config in self:
            if not (1 <= config.timeout_express <= 120):
                raise ValidationError(_(
                    'Payment timeout must be between 1 and 120 minutes'
                ))
    
    @api.constrains('webhook_url')
    def _check_webhook_url(self):
        """
        Validate webhook URL format
        
        Must be a valid HTTPS URL accessible from internet
        """
        for config in self:
            if config.webhook_url:
                if not config.webhook_url.startswith('https://'):
                    raise ValidationError(_(
                        'Webhook URL must use HTTPS for security'
                    ))
    
    def action_test_connection(self):
        """
        Test API connection and credentials
        
        This method:
        1. Attempts to get fabric token from Telebirr
        2. Updates test result fields
        3. Returns user-friendly notification
        
        Returns:
            Dictionary for client action notification
        """
        self.ensure_one()
        
        try:
            # Import service here to avoid circular imports
            from ..services.fabric_token_service import TelebirrFabricTokenService
            
            # Test connection by getting fabric token
            token_service = TelebirrFabricTokenService(self.env)
            result = token_service.get_fabric_token(self)
            
            # Update test result
            self.write({
                'last_test_time': datetime.now(),
                'last_test_result': json.dumps({
                    'status': 'success',
                    'message': 'Connection test successful',
                    'token_obtained': bool(result.get('token')),
                    'effective_date': result.get('effectiveDate'),
                    'expiration_date': result.get('expirationDate')
                }, indent=2)
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Telebirr connection test successful!'),
                    'type': 'success',
                    'sticky': False,
                }
            }
            
        except Exception as e:
            # Update test result with error
            self.write({
                'last_test_time': datetime.now(),
                'last_test_result': json.dumps({
                    'status': 'error',
                    'message': str(e),
                    'timestamp': datetime.now().isoformat()
                }, indent=2)
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Test Failed'),
                    'message': _('Failed to connect to Telebirr: %s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }
    
    def action_view_transactions(self):
        """
        View all transactions using this configuration
        
        Returns:
            Action to display transaction list filtered by this config
        """
        self.ensure_one()
        
        return {
            'type': 'ir.actions.act_window',
            'name': _('Telebirr Transactions'),
            'res_model': 'telebirr.transaction',
            'view_mode': 'tree,form',
            'domain': [('config_id', '=', self.id)],
            'context': {'default_config_id': self.id},
        }
    
    def get_active_config(self, company_id=None):
        """
        Get active configuration for a company
        
        Args:
            company_id: Company ID (optional, uses current company if not provided)
            
        Returns:
            telebirr.config recordset or empty recordset
        """
        if not company_id:
            company_id = self.env.company.id
        
        return self.search([
            ('active', '=', True),
            ('company_id', '=', company_id)
        ], limit=1)
    
    @api.model
    def get_default_config(self):
        """
        Get default configuration for current company
        
        Returns:
            telebirr.config record or False
        """
        company_id = self.env.company.id
        return self.get_active_config(company_id)
    
    def _format_key_for_display(self, key_type='private'):
        """
        Format RSA key for display in UI
        
        Args:
            key_type: 'private' or 'public'
            
        Returns:
            Formatted key string for display
        """
        key_field = self.private_key if key_type == 'private' else self.public_key
        
        if not key_field:
            return ''
        
        # Show first and last few characters for security
        if len(key_field) > 50:
            return f"{key_field[:20]}...{key_field[-20:]}"
        
        return key_field
    
    def action_generate_test_keys(self):
        """
        Generate test RSA key pair for development
        
        This method creates a test key pair for development/testing.
        NEVER use in production!
        
        Returns:
            Client action notification
        """
        self.ensure_one()
        
        if self.environment == 'prod':
            raise UserError(_(
                'Cannot generate test keys for production environment!'
            ))
        
        try:
            from Crypto.PublicKey import RSA
            from Crypto import Random
            
            # Generate 2048-bit RSA key pair
            key = RSA.generate(2048, Random.new().read)
            
            # Export keys in PEM format
            private_key = key.exportKey().decode('utf-8')
            public_key = key.publickey().exportKey().decode('utf-8')
            
            self.write({
                'private_key': private_key,
                'public_key': public_key
            })
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Test Keys Generated'),
                    'message': _('Test RSA key pair generated successfully.'),
                    'type': 'success',
                }
            }
            
        except ImportError:
            raise UserError(_(
                'PyCryptodome library is required for key generation'
            ))
        except Exception as e:
            raise UserError(_(
                'Failed to generate keys: %s'
            ) % str(e))
