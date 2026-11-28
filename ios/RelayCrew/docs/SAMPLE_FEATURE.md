# Sample Feature

A complete vertical slice — a **Profile** feature that fetches a user from an API and shows it — walking through every layer: Domain → Data → Presentation → App.

## 1. Domain (pure, no frameworks)

`Domain/Models/User.swift`

```swift
struct User: Equatable {
    let id: String
    let name: String
    let email: String
}
```

`Domain/RepositoryProtocols/UserRepository.swift`

```swift
protocol UserRepository {
    func fetchProfile() async throws -> User
}
```

`Domain/UseCases/FetchProfileUseCase.swift`

```swift
struct FetchProfileUseCase {
    let repository: UserRepository

    func execute() async throws -> User {
        try await repository.fetchProfile()
    }
}
```

## 2. Data (implements the Domain protocols)

### DTOs

`Data/Network/DTO/Response/UserResponseDTO.swift`

```swift
struct UserResponseDTO: Decodable, Sendable {
    let id: String
    let name: String
    let email: String
}
```

### Endpoint + API service (RESTKit)

With [RESTKit](https://github.com/konotori/RESTKit) an endpoint declares its response type, so `client.request(...)` returns the decoded model — no casting, no runtime type errors.

`Data/Network/Services/UserAPI.swift`

```swift
import RESTKit

struct GetUserProfile: Endpoint {
    typealias Response = JSON<UserResponseDTO>   // ← what this endpoint returns

    let userId: String

    var baseURL: String { "https://api.example.com" }
    var path: String { "/users/\(userId)" }
    var method: HTTPMethod { .get }
}

protocol UserAPI {
    func getProfile() async throws -> UserResponseDTO
}

struct RestUserAPI: UserAPI {
    let client: APIClientProtocol

    init(client: APIClientProtocol = APIClient()) {
        self.client = client
    }

    func getProfile() async throws -> UserResponseDTO {
        try await client.request(GetUserProfile(userId: "me"))   // returns UserResponseDTO
    }
}
```

### Mapper

`Data/Mappers/UserMapper.swift`

```swift
enum UserMapper {
    static func map(_ dto: UserResponseDTO) -> User {
        User(id: dto.id, name: dto.name, email: dto.email)
    }
}
```

### Repository implementation

`Data/Repositories/UserRepositoryImpl.swift`

```swift
struct UserRepositoryImpl: UserRepository {
    let api: UserAPI

    func fetchProfile() async throws -> User {
        let dto = try await api.getProfile()
        return UserMapper.map(dto)
    }
}
```

## 3. Presentation (SwiftUI)

`Presentation/Screens/ProfileScreen.swift`

```swift
import SwiftUI

struct ProfileScreen: View {
    let fetchProfile: FetchProfileUseCase

    @State private var user: User?
    @State private var error: String?

    var body: some View {
        VStack(spacing: 16) {
            if let user {
                Text(user.name)
                Text(user.email).foregroundStyle(.secondary)
            } else if let error {
                Text(error).foregroundStyle(.red)
            } else {
                ProgressView()
            }
        }
        .task {
            do {
                user = try await fetchProfile.execute()
            } catch is CancellationError {
                // view disappeared — ignore
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
```

## 4. App (composition root)

`App/AppContainer.swift`

```swift
@MainActor
final class AppContainer {
    static let shared = AppContainer()

    lazy var userAPI: UserAPI = RestUserAPI()
    lazy var userRepository: UserRepository = UserRepositoryImpl(api: userAPI)
    lazy var fetchProfileUseCase = FetchProfileUseCase(repository: userRepository)
}
```

Then build the screen with its dependency injected:

```swift
ProfileScreen(fetchProfile: AppContainer.shared.fetchProfileUseCase)
```

## Notes

- **Navigation:** to push this screen, add a case to your route enum and present it via [NaviStack](https://github.com/konotori/NaviStack) (`router.push(.profile)`), wired in `Presentation/Navigation/`.
- **Logging:** log inside the repository/use case with [LogPipe](https://github.com/konotori/LogPipe) (`Log.network.info("Fetched profile", context: ["id": user.id])`).
- **Testing:** because the use case depends on the `UserRepository` *protocol*, you can unit-test it with a stub repository — no networking required.
