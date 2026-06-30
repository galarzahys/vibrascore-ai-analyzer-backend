"""
Serviço de armazenamento AWS S3
"""

import boto3
import os
import uuid
from botocore.exceptions import ClientError

s3_client = boto3.client(
    "s3",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "vibrascore-documents")


def upload_file(file_bytes: bytes, filename: str, analysis_id: str, field_key: str) -> str:
    """
    Faz upload do arquivo para S3 e retorna a chave S3.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    s3_key = f"analyses/{analysis_id}/{field_key}/{uuid.uuid4()}.{ext}"

    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=file_bytes,
        ContentType=_get_content_type(ext),
        ServerSideEncryption="AES256",
    )

    return s3_key


def get_presigned_url(s3_key: str, expires: int = 3600) -> str:
    """
    Gera URL pré-assinada para download temporário.
    """
    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": s3_key},
            ExpiresIn=expires,
        )
        return url
    except ClientError:
        return ""


def download_file(s3_key: str) -> bytes:
    """
    Baixa o arquivo do S3 e retorna os bytes.
    """
    response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
    return response["Body"].read()


def delete_file(s3_key: str) -> bool:
    try:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)
        return True
    except ClientError:
        return False


def create_bucket_if_not_exists():
    """
    Cria o bucket S3 se não existir. Chamar no startup.
    """
    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
    except ClientError:
        s3_client.create_bucket(Bucket=BUCKET_NAME)
        # Bloquear acesso público
        s3_client.put_public_access_block(
            Bucket=BUCKET_NAME,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )


def _get_content_type(ext: str) -> str:
    mapping = {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "heic": "image/heic",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "wav": "audio/wav",
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return mapping.get(ext, "application/octet-stream")


def get_presigned_upload_url(analysis_id: str, field_key: str, filename: str, content_type: str, expires: int = 600) -> dict:
    """
    Gera URL pré-assinada para upload direto do navegador ao S3.
    Retorna dict com upload_url e s3_key.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    s3_key = f"analyses/{analysis_id}/{field_key}/{uuid.uuid4()}.{ext}"

    upload_url = s3_client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": s3_key,
            "ContentType": content_type or _get_content_type(ext),
            "ServerSideEncryption": "AES256",
        },
        ExpiresIn=expires,
    )

    return {"upload_url": upload_url, "s3_key": s3_key}