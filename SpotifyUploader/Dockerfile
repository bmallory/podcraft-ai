# 1. Use an official AWS Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies and download save-to-spotify CLI
RUN dnf install -y tar xz wget unzip && \
    cd /tmp && \
    wget https://github.com/spotify/save-to-spotify/releases/download/v0.1.1/save-to-spotify-linux-amd64-v0.1.1.zip && \
    unzip save-to-spotify-linux-amd64-v0.1.1.zip && \
    mkdir -p /opt/bin && \
    cp save-to-spotify /opt/bin/ && \
    chmod +x /opt/bin/save-to-spotify && \
    rm -rf /tmp/* && \
    dnf clean all

# 2. Copy requirements.txt and install Python dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# 3. Copy function code
COPY app.py ${LAMBDA_TASK_ROOT}/

# 4. Set the CMD to your handler (file_name.function_name)
CMD [ "app.handler" ]
