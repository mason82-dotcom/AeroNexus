# Build context: upstream/backend (patched DJI-Cloud-API-Demo), copied there by setup.sh
FROM maven:3.9.9-eclipse-temurin-11 AS build
WORKDIR /src
COPY . .
RUN mvn -B -q -DskipTests -Dlombok.version=1.18.36 clean package

FROM eclipse-temurin:11.0.28_6-jre
WORKDIR /app
COPY --from=build /src/sample/target/sample-*.jar /app/app.jar
# /app/config/application.yml is mounted by docker-compose and overrides the built-in one
EXPOSE 6789
ENTRYPOINT ["java", "-Xms256m", "-Xmx768m", "-jar", "/app/app.jar"]
