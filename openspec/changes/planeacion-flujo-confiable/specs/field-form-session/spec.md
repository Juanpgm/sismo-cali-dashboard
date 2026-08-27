# Delta for field-form-session

## ADDED Requirements

### Requirement: Assigned-point card shows reporter contact

The assigned-point card MUST display the reporter's name and phone number when the point carries
them, and MUST render the phone as a `tel:` link so the field crew can call with one tap. When
contact data is absent, the card MUST render without the contact block and without a broken link.

#### Scenario: Contact present shows name and a working call button
- GIVEN an assigned point whose contact channel has `nombre_solicitante` and `telefono_solicitante`
- WHEN the card renders
- THEN it shows the reporter's name and a `tel:` link built from the phone number

#### Scenario: Missing contact renders no broken link
- GIVEN an assigned point with no contact data
- WHEN the card renders
- THEN it shows no contact block and no `tel:` link
