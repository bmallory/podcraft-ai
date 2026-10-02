# 1. Use an official AWS Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# Install FFmpeg static build
RUN dnf install -y tar xz wget && \
    cd /tmp && \
    wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar xf ffmpeg-release-amd64-static.tar.xz && \
    mkdir -p /opt/bin && \
    cp ffmpeg-*-static/ffmpeg /opt/bin/ && \
    cp ffmpeg-*-static/ffprobe /opt/bin/ && \
    rm -rf /tmp/ffmpeg* && \
    dnf clean all

# 2. Copy requirements.txt and install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# 3. Copy handler
COPY app.py ${LAMBDA_TASK_ROOT}/

# 4. Set CMD to handler
CMD [ "app.handler" ]
