# User Template Engine (`base_user_template_engine`) — Odoo 18

Provision users from layered, inheritable templates of preferences and access rights,
targeted by user tags and applied automatically or in bulk.

## Data model

| Model | Purpose |
|---|---|
| `res.users.template` | A template: targeting, precedence, inheritance, and per-attribute value + policy + mode |
| `res.users.tag` | Logical user grouping (`res_users_tag_user_rel`), drives targeting and bulk actions |
| `res.users.template.log` | Audit trail of every applied change (user, templates, trigger, before → after) |
| `res.users.template.apply.wizard` | Bulk application with dry-run preview |
| `res.users` (inherited) | `tag_ids`, computed `applicable_template_ids`, `template_log_ids` |

### Managed attributes

| Attribute | User field | Kind |
|---|---|---|
| Language | `lang` | scalar |
| Timezone | `tz` | scalar |
| Home Action | `action_id` | scalar |
| Allowed Companies | `company_ids` | multi |
| Default Company | `company_id` | scalar |
| Access Groups | `groups_id` | multi |
| Revoked Groups | `groups_id` | subtract |

Each attribute has a **policy** (`skip` / `own` / `inherit`) and a **mode**
(`default` → template's `apply_mode`, `merge`, `override`).

## Resolution rules

1. **Applicable templates**: active, and either `scope = global` (internal users; portal
   optional) or targeted by tag / explicit user; owner company must be among the user's companies.
2. **Order**: ascending `(sequence, id)`. Later templates have higher precedence.
3. **Inheritance**: `inherit` walks up `parent_id` to the first ancestor with `own` (or stops at `skip`).
   The *mode* is always the applied (child) template's mode.
4. **Merge vs override**
   - scalar: override always writes; merge only fills an empty value.
   - multi: override replaces; merge unions.
   - revocations always subtract.
5. **Safety**
   - User-type groups (Internal/Portal/Public) are never granted, revoked or replaced.
   - Share (portal) users never receive access groups.
   - `protect_admins` keeps Settings administrators' groups untouched.
   - Default company is forced into allowed companies (kept if explicitly enforced, otherwise
     the first allowed company is used).
   - Implied-group closure makes the engine idempotent: re-applying produces no writes.
6. **Batching**: plans are computed in memory, then users with identical plans are written in one call.

## Triggers

- `res.users.create` and `res.users.write` (on `tag_ids`), and tag-side membership edits →
  templates with `auto_apply`.
- "Apply Templates Now" on the user form, "Apply to Users" on a template,
  "Apply Templates" on a tag, and the **Action** menu on user/tag lists → bulk wizard.
- Scheduled action "User Templates: re-synchronise all users" (inactive by default).

## Security

- New group **User Template Manager** (implies *Access Rights*); every *Settings* user gets it.
- `tag_ids` on users is restricted to managers, so tag-driven sudo application cannot be used
  for privilege escalation.
- Multi-company record rule on templates (`company_id`).

## Tests

```
odoo-bin -d <db> -i base_user_template_engine --test-enable --test-tags /base_user_template_engine --stop-after-init
```

26 `TransactionCase` tests cover targeting, precedence, merge/override conflicts, multi-level
inheritance, cycle detection, group safety, company consistency, auto-apply hooks, the wizard
dry-run (including the live onchange preview), idempotency and language application.
