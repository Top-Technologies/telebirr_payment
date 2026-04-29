# -*- coding: utf-8 -*-
{
    'name': 'Telebirr Payment Integration',
    'version': '18.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Telebirr C2B Payment Gateway Integration',
    'description': '''
        Enable Telebirr mobile payments in Odoo 18e.
        This module integrates with Telebirr's C2B payment gateway to allow
        Ethiopian businesses to accept mobile payments through Telebirr.
        
        Features:
        - Payment request from Sales Orders and Invoices
        - Web checkout integration with Telebirr
        - Automatic payment reconciliation
        - Webhook notifications
        - Refund processing
        - Comprehensive error handling
    ''',
    'author': 'Natnael Yonas, Top Tech Solutions',
    'website': 'https://toptech-world.com/',
    'license': 'LGPL-3',
    'depends': [
        'account',
        'sale_management',
        'payment',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/payment_acquirer_data.xml',
        'views/telebirr_config_views.xml',
        'views/telebirr_transaction_views.xml',
        'views/payment_acquirer_views.xml',
        'views/payment_wizard_views.xml',
        'templates/payment_templates.xml',
    ],
    'demo': [],
    'installable': True,
    'auto_install': False,
    'application': True,
    'external_dependencies': {
        'python': [
            'requests',
            'pycryptodome',
        ],
    },
    'images': ['static/description/banner.png'],
}
