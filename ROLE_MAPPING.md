# TrustCart Dashboard Role Mapping

## Customer / User
Reference: `ui_reference/customer_dashboard_reference.png`

Purpose: shopping only.
- Home
- My Basket
- Scan Product
- Store Map
- Settings
- Live scan status
- Recent activity
- Checkout

No store-wide alerts, AI training, camera fleet controls or staff incident resolution.

## Staff / Store Manager
Reference: `ui_reference/manager_dashboard_reference.png`

Purpose: operate one store.
- Live store view
- Alerts & incidents
- Customer activity
- Products & inventory
- Scan vs pick analytics
- Loss-prevention impact
- Resolve/nudge/escalate alerts
- Reports
- AI model status
- Settings

The supplied light dashboard is therefore correctly aligned with the manager/staff operational role.

## Admin
Reference: `ui_reference/admin_dark_dashboard_reference.png`

Purpose: platform administration.
- Users & roles
- Multi-system configuration
- AI model training
- Device/system health
- Store-level analytics
- Security/settings

The supplied dark dashboard is correctly aligned with the admin role.

## Staff dashboard note
The current application maps `/staff` to the dedicated incident/staff view. The manager dashboard is the store-wide operational dashboard. This separation is intentional: staff resolve incidents; managers monitor the store and business metrics; admins control the platform.
