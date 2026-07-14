# Cosmos DB Data Model

## Database Structure

```
Database: IncidentRCA
├── Container: historical-incidents (partition: /serviceKey)
└── Container: change-records (partition: /serviceKey)
```

## Partition Strategy

All containers use **`serviceKey`** as the partition key:
- Normalized lowercase service identifiers
- Examples: `mobile-banking`, `online-banking`, `payments-platform`
- Enables efficient queries within a service boundary
- Reduces RU consumption for service-specific searches

## Historical Incident Document

```json
{
  "id": "INC10001",
  "documentType": "historicalIncident",
  "serviceKey": "mobile-banking",
  "incidentId": "INC10001",
  "incidentTitle": "Mobile Banking Login Failures",
  "incidentDescription": "Users unable to log in to mobile app...",
  "severity": "P2",
  "businessService": "Mobile Banking",
  "applicationName": "Mobile Banking API",
  "configurationItem": "MB-API-PROD-01",
  "symptoms": [
    "login_failure",
    "authentication_error",
    "timeout"
  ],
  "errorCodes": ["AUTH-401", "TIMEOUT-504"],
  "rootCause": "OAuth token service degradation due to database connection pool exhaustion",
  "rootCauseCategory": "Application",
  "resolutionSummary": "Increased connection pool size and added circuit breaker",
  "linkedChangeId": "CHG50001",
  "tags": [
    "mobile_banking",
    "authentication",
    "oauth",
    "database",
    "connection_pool"
  ],
  "searchText": "mobile banking mobile banking api login failure authentication error...",
  "isResolved": true
}
```

### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Primary key, same as incidentId |
| `documentType` | string | Yes | Always "historicalIncident" |
| `serviceKey` | string | Yes | Partition key, normalized service name |
| `incidentId` | string | Yes | Business incident identifier |
| `incidentTitle` | string | Yes | Short incident description |
| `incidentDescription` | string | Yes | Detailed incident description |
| `severity` | string | Yes | P1-P4 priority level |
| `businessService` | string | Yes | Display name of service |
| `applicationName` | string | No | Specific application affected |
| `configurationItem` | string | No | Infrastructure component (CI) |
| `symptoms` | array | No | Observed symptoms/keywords |
| `errorCodes` | array | No | Technical error codes |
| `rootCause` | string | Yes | Identified root cause |
| `rootCauseCategory` | string | Yes | Category (Network, Application, Database, etc.) |
| `resolutionSummary` | string | No | How it was resolved |
| `linkedChangeId` | string | No | Related change record ID |
| `tags` | array | No | Searchable tags/keywords |
| `searchText` | string | Yes | Concatenated searchable text |
| `isResolved` | boolean | Yes | Resolution status (always true for historical) |

## Change Record Document

```json
{
  "id": "CHG50001",
  "documentType": "changeRecord",
  "serviceKey": "mobile-banking",
  "changeId": "CHG50001",
  "changeTitle": "Increase OAuth Database Connection Pool",
  "changeDescription": "Scale up database connection pool for OAuth service...",
  "changeType": "Normal",
  "changeCategory": "Application",
  "changeStatus": "Completed",
  "businessService": "Mobile Banking",
  "applicationName": "OAuth Service",
  "configurationItem": "MB-OAUTH-PROD",
  "implementationSummary": "Updated connection pool from 50 to 200 connections",
  "rollbackPerformed": false,
  "validationResult": "Partially Successful",
  "postImplementationIssues": [
    "Initial spike in connection timeouts observed",
    "Resolved after connection pool warmup"
  ],
  "relatedIncidentIds": ["INC10001", "INC10002"],
  "changeCorrelationNotes": "Incidents started 2 hours after change deployment",
  "tags": [
    "oauth",
    "database",
    "connection_pool",
    "performance"
  ],
  "searchText": "mobile banking oauth service database connection pool..."
}
```

### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Primary key, same as changeId |
| `documentType` | string | Yes | Always "changeRecord" |
| `serviceKey` | string | Yes | Partition key, normalized service name |
| `changeId` | string | Yes | Business change identifier |
| `changeTitle` | string | Yes | Short change description |
| `changeDescription` | string | Yes | Detailed change description |
| `changeType` | string | Yes | Normal, Emergency, Standard |
| `changeCategory` | string | Yes | Network, Application, Database, etc. |
| `changeStatus` | string | Yes | Completed, Failed, Rolled Back |
| `businessService` | string | Yes | Display name of service |
| `applicationName` | string | No | Specific application modified |
| `configurationItem` | string | No | Infrastructure component (CI) |
| `implementationSummary` | string | No | What was changed |
| `rollbackPerformed` | boolean | Yes | Whether change was rolled back |
| `validationResult` | string | No | Successful, Partially Successful, Failed |
| `postImplementationIssues` | array | No | Issues observed after change |
| `relatedIncidentIds` | array | No | Incidents linked to this change |
| `changeCorrelationNotes` | string | No | Correlation analysis notes |
| `tags` | array | No | Searchable tags/keywords |
| `searchText` | string | Yes | Concatenated searchable text |

## Relationships

### Incident-to-Change Relationship

```
Incidents → linkedChangeId → Changes.changeId
```

- **One-to-One**: Each incident can link to at most one change
- **Optional**: Not all incidents have related changes
- **Application-enforced**: No database-level foreign key
- **Validation**: All `linkedChangeId` values must exist in change-records

### Reverse Relationship

```
Changes → relatedIncidentIds → [Incidents.incidentId]
```

- **One-to-Many**: Each change can relate to multiple incidents
- **Optional**: Not all changes have related incidents
- **Application-enforced**: No automatic reverse lookup

## Service Key Mapping

| Business Service | Service Key |
|------------------|-------------|
| Mobile Banking | `mobile-banking` |
| Online Banking | `online-banking` |
| Payments Platform | `payments-platform` |
| Regulatory Reporting Platform | `regulatory-reporting-platform` |
| Fraud Detection Platform | `fraud-detection-platform` |
| Customer Profile Service | `customer-profile-service` |
| Credit Card Processing | `credit-card-processing` |
| Data Warehouse Platform | `data-warehouse-platform` |
| Mortgage Platform | `mortgage-platform` |
| Treasury Platform | `treasury-platform` |
| Wire Transfer System | `wire-transfer-system` |

## Query Patterns

### Efficient (Partition-Scoped)

```sql
-- Query incidents by service
SELECT * FROM c 
WHERE c.serviceKey = "mobile-banking" 
AND c.isResolved = true

-- Get specific incident
SELECT * FROM c 
WHERE c.id = "INC10001"
-- Partition key: "mobile-banking"

-- Query changes for service
SELECT * FROM c 
WHERE c.serviceKey = "mobile-banking" 
AND c.changeStatus = "Completed"
```

### Expensive (Cross-Partition)

```sql
-- Query all resolved incidents (avoid if possible)
SELECT * FROM c 
WHERE c.isResolved = true 
AND c.documentType = "historicalIncident"
```

## Data Volume (Demo)

- **Historical Incidents**: 30 documents
- **Change Records**: 15 documents
- **Services**: 11 unique services
- **Incident-Change Links**: 15 valid relationships

## Root Cause Categories

- Application
- Database
- Network
- Infrastructure
- Configuration
- Security
- Integration
- Data
- Unknown

## Change Categories

- Application
- Database
- Network
- Infrastructure
- Security
- Configuration
- Data

## Indexing Strategy

### Default Cosmos DB Indexing
- All paths indexed by default
- Efficient for equality and range queries
- Sufficient for demo workload

### Future Optimization (Phase 5)
- Vector indexing on embeddings
- Full-text search policies
- Exclude large text fields from automatic indexing
- Composite indexes for common query patterns
